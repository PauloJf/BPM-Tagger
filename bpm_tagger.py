#!/usr/bin/env python3
"""BPM Tagger for Navidrome — detects BPM, writes tags, tracks results in SQLite."""

import csv
import hashlib
import json
import logging
import os
import secrets
import sqlite3
import sys
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

def _read_version() -> str:
    # VERSION file is present in Docker images and source checkouts
    vf = Path(__file__).parent / "VERSION"
    if vf.is_file():
        v = vf.read_text().strip()
        if v:
            return v.lstrip("v")
    # Fall back to git tag when running directly from a git checkout
    try:
        import subprocess
        v = subprocess.check_output(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=str(Path(__file__).parent),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if v:
            return v.lstrip("v")
    except Exception:
        pass
    return "dev"

__version__ = _read_version()

# Suppress noisy but harmless third-party warnings:
# - libsndfile can't decode MP3; librosa falls back to audioread (ffmpeg) automatically
# - audioread fallback is deprecated in librosa 0.10 but still works until 1.0
warnings.filterwarnings("ignore", message="PySoundFile failed")
warnings.filterwarnings("ignore", category=FutureWarning, module="librosa")
warnings.filterwarnings("ignore", message="Using padding='same' with even kernel lengths")

import librosa
import mutagen
import numpy as np
import requests
from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError, TBPM
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".mp3", ".flac", ".ogg", ".m4a", ".aac", ".wav", ".opus", ".wv"}


def settings_file_path(db_path: str) -> str:
    return str(Path(db_path).parent / "settings.json")


def load_settings_override(config: dict) -> dict:
    """Merge persisted settings.json overrides into config (overwrites env-var values)."""
    path = settings_file_path(config["db_path"])
    if os.path.isfile(path):
        try:
            with open(path) as f:
                config.update(json.load(f))
        except Exception as exc:
            log.warning("Could not load settings override: %s", exc)
    return config

# ---------------------------------------------------------------------------
# Low-level BPM helpers
# ---------------------------------------------------------------------------

_local = threading.local()


class ScanProgress:
    STEPS = ("deeprhythm", "essentia", "librosa")

    def __init__(self):
        self._lock = threading.Lock()
        self.cumulative_completed = 0
        self._reset()

    def _reset(self):
        self.is_scanning  = False
        self.is_paused    = False
        self.current_file = ""
        self.current_step = ""
        self.completed    = 0
        self.total        = 0
        self.last_file    = ""
        self.last_bpm     = None

    def set_paused(self, paused: bool):
        with self._lock:
            self.is_paused = paused

    def start(self, total: int):
        with self._lock:
            self._reset(); self.is_scanning = True; self.total = total

    def set_file(self, path: str):
        with self._lock:
            self.current_file = Path(path).name; self.current_step = ""

    def set_step(self, step: str):
        with self._lock:
            self.current_step = step

    def finish_file(self, path: str, bpm: Optional[float]):
        with self._lock:
            self.completed += 1
            self.cumulative_completed += 1
            self.last_file = Path(path).name; self.last_bpm = bpm
            self.current_file = ""; self.current_step = ""

    def finish(self):
        with self._lock:
            self.is_scanning = False; self.current_file = ""; self.current_step = ""

    def snapshot(self) -> dict:
        with self._lock:
            si = (self.STEPS.index(self.current_step) + 1
                  if self.current_step in self.STEPS else 0)
            return dict(is_scanning=self.is_scanning, is_paused=self.is_paused,
                        current_file=self.current_file,
                        current_step=self.current_step, step_index=si,
                        step_total=len(self.STEPS), completed=self.completed,
                        total=self.total, cumulative_completed=self.cumulative_completed,
                        last_file=self.last_file, last_bpm=self.last_bpm)


def _get_predictor():
    if not hasattr(_local, "predictor"):
        from deeprhythm import DeepRhythmPredictor
        _local.predictor = DeepRhythmPredictor()
    return _local.predictor


def _detect_bpm_deeprhythm(file_path: str) -> float:
    return round(float(_get_predictor().predict(file_path)), 1)


def _detect_bpm_essentia(file_path: str) -> Optional[float]:
    """Return BPM from essentia RhythmExtractor2013 (multifeature), or None on failure."""
    try:
        import essentia.standard as es
        audio = es.EasyLoader(filename=file_path, sampleRate=44100)()
        bpm, _, _, _, _ = es.RhythmExtractor2013(method="multifeature")(audio)
        return round(float(bpm), 1)
    except Exception as exc:
        log.warning("essentia failed for %s: %s", Path(file_path).name, exc)
        return None


def _track_duration(file_path: str) -> Optional[float]:
    """Return track length in seconds, or None if the format is unsupported."""
    audio = mutagen.File(file_path)
    if audio and hasattr(audio.info, "length") and audio.info.length > 0:
        return audio.info.length
    return None


def _librosa_window(file_path: str, offset: float, duration: float) -> tuple[float, float]:
    y, sr = librosa.load(file_path, offset=offset, duration=duration, sr=None, mono=True)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, aggregate=np.median)
    candidates = librosa.feature.tempo(onset_envelope=onset_env, sr=sr, aggregate=None)
    bpm = float(np.median(candidates)) if len(candidates) > 0 else 0.0
    _, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, trim=False)
    if len(beats) > 2:
        intervals = np.diff(librosa.frames_to_time(beats, sr=sr))
        conf = float(np.clip(1.0 - np.std(intervals) / (np.mean(intervals) + 1e-9), 0.0, 1.0))
    else:
        conf = 0.0
    return round(bpm, 1), conf


def _detect_bpm_librosa_multiseg(file_path: str, n_segments: int, seg_duration: float) -> tuple[float, float]:
    total = _track_duration(file_path)

    if total is None or total < seg_duration * 1.5:
        return _librosa_window(file_path, 0.0, 180.0)

    offsets = [
        max(0.0, min(total * (i + 1) / (n_segments + 1) - seg_duration / 2, total - seg_duration))
        for i in range(n_segments)
    ]
    bpms, confs = zip(*[_librosa_window(file_path, o, seg_duration) for o in offsets])
    return round(float(np.median(bpms)), 1), float(np.median(confs))


# ---------------------------------------------------------------------------
# Reconciliation + plausibility
# ---------------------------------------------------------------------------

def _reconcile(bpm_dr: Optional[float], bpm_es: Optional[float],
               bpm_lb: float, config: dict) -> tuple[float, bool]:
    """Return (final_bpm, needs_review) from deeprhythm, essentia, and librosa values."""
    threshold = config["review_disagree_threshold"]
    bpm_min, bpm_max = config["bpm_min"], config["bpm_max"]
    center = (bpm_min + bpm_max) / 2

    def _close(a: float, b: float) -> bool:
        return abs(a - b) <= threshold

    def _is_octave(a: float, b: float) -> bool:
        if a <= 0 or b <= 0:
            return False
        return 1.9 < max(a, b) / min(a, b) < 2.1

    def _fix_octave(a: float, b: float) -> float:
        for v in (a, b):
            if bpm_min <= v <= bpm_max:
                return v
        return min(a, b, key=lambda x: abs(x - center))

    # Both neural detectors available — primary path
    if bpm_dr is not None and bpm_es is not None:
        if config["octave_correction"] and _is_octave(bpm_dr, bpm_es):
            return _fix_octave(bpm_dr, bpm_es), False
        if _close(bpm_dr, bpm_es):
            return round((bpm_dr + bpm_es) / 2, 1), False
        # Detectors disagree — use librosa as tiebreaker, flag for review
        if bpm_lb > 0:
            chosen = bpm_dr if abs(bpm_dr - bpm_lb) <= abs(bpm_es - bpm_lb) else bpm_es
        else:
            chosen = bpm_dr
        return chosen, True

    # Only deeprhythm
    if bpm_dr is not None:
        if bpm_lb <= 0:
            return bpm_dr, True
        if config["octave_correction"] and _is_octave(bpm_dr, bpm_lb):
            return _fix_octave(bpm_dr, bpm_lb), False
        return bpm_dr, not _close(bpm_dr, bpm_lb)

    # Only essentia
    if bpm_es is not None:
        if bpm_lb <= 0:
            return bpm_es, True
        if config["octave_correction"] and _is_octave(bpm_es, bpm_lb):
            return _fix_octave(bpm_es, bpm_lb), False
        return bpm_es, not _close(bpm_es, bpm_lb)

    # Librosa only — both neural detectors failed
    return bpm_lb, True


def _normalize_bpm(bpm: float, bpm_min: float, bpm_max: float) -> float:
    """Halve/double until BPM is inside [bpm_min, bpm_max]."""
    if bpm <= 0 or bpm_min <= 0 or bpm_max <= 0 or bpm_min >= bpm_max:
        return round(bpm, 1)
    for _ in range(64):
        if bpm >= bpm_min:
            break
        bpm *= 2
    for _ in range(64):
        if bpm <= bpm_max:
            break
        bpm /= 2
    return round(bpm, 1)


# ---------------------------------------------------------------------------
# Public BPM detection entry point
# ---------------------------------------------------------------------------

def detect_bpm(file_path: str, config: dict, progress: Optional[ScanProgress] = None) -> dict:
    """
    Return dict with keys: bpm, bpm_dr, bpm_es, bpm_lb, confidence, detector, needs_review.
    Runs deeprhythm + essentia as primary detectors; librosa as confidence/tiebreaker.
    """
    n_seg = int(config.get("multi_segment_count", 3))
    seg_dur = float(config.get("segment_duration", 45))

    bpm_dr: Optional[float] = None
    if config.get("use_deeprhythm", True):
        if progress: progress.set_step("deeprhythm")
        try:
            bpm_dr = _detect_bpm_deeprhythm(file_path)
        except Exception as exc:
            log.warning("deeprhythm failed for %s: %s", Path(file_path).name, exc)

    bpm_es: Optional[float] = None
    if config.get("use_essentia", True):
        if progress: progress.set_step("essentia")
        bpm_es = _detect_bpm_essentia(file_path)  # handles exceptions internally

    if progress: progress.set_step("librosa")
    if config.get("multi_segment", True):
        bpm_lb, conf_lb = _detect_bpm_librosa_multiseg(file_path, n_seg, seg_dur)
    else:
        bpm_lb, conf_lb = _librosa_window(file_path, 0.0, 180.0)

    bpm_final, needs_review = _reconcile(bpm_dr, bpm_es, bpm_lb, config)

    if bpm_dr is not None and bpm_es is not None:
        detector = "deeprhythm+essentia"
    elif bpm_dr is not None:
        detector = "deeprhythm+librosa"
    elif bpm_es is not None:
        detector = "essentia+librosa"
    else:
        detector = "librosa"

    bpm_final = _normalize_bpm(bpm_final, config["bpm_min"], config["bpm_max"])

    return {
        "bpm":          bpm_final,
        "bpm_dr":       bpm_dr,
        "bpm_es":       bpm_es,
        "bpm_lb":       bpm_lb,
        "confidence":   conf_lb,
        "detector":     detector,
        "needs_review": needs_review,
    }


# ---------------------------------------------------------------------------
# Tag writing
# ---------------------------------------------------------------------------

def write_bpm_tag(file_path: str, bpm: float) -> bool:
    ext = Path(file_path).suffix.lower()
    bpm_str = str(round(bpm))
    try:
        if ext == ".mp3":
            try:
                tags = ID3(file_path)
            except ID3NoHeaderError:
                tags = ID3()
            tags["TBPM"] = TBPM(encoding=3, text=bpm_str)
            tags.save(file_path)
        elif ext == ".flac":
            audio = FLAC(file_path)
            audio["BPM"] = bpm_str
            audio.save()
        elif ext in (".m4a", ".aac"):
            audio = MP4(file_path)
            audio["tmpo"] = [round(bpm)]
            audio.save()
        elif ext in (".ogg", ".opus"):
            audio = OggVorbis(file_path)
            audio["BPM"] = bpm_str
            audio.save()
        else:
            audio = mutagen.File(file_path)
            if audio is None:
                return False
            audio["BPM"] = bpm_str
            audio.save()
        return True
    except Exception as exc:
        log.error("Failed to write tag for %s: %s", file_path, exc)
        return False


# ---------------------------------------------------------------------------
# File hash (fast change detection using size + mtime)
# ---------------------------------------------------------------------------

def get_file_hash(file_path: str) -> str:
    stat = os.stat(file_path)
    return f"{stat.st_size}:{stat.st_mtime}"


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

class BPMDatabase:
    _SUSPICIOUS_WHERE = """status = 'done' AND locked = 0 AND (
        needs_review = 1
        OR (bpm_confidence IS NOT NULL AND bpm_confidence < ?)
        OR detector = 'librosa'
        OR (bpm IS NOT NULL AND (bpm < ? OR bpm > ?))
    )"""
    _SUSPICIOUS_COLS = (
        "file_path, bpm, bpm_dr, bpm_es, bpm_lb, bpm_confidence, detector, needs_review"
    )

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tracks (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path      TEXT UNIQUE NOT NULL,
                    file_hash      TEXT,
                    bpm            REAL,
                    bpm_dr         REAL,
                    bpm_es         REAL,
                    bpm_lb         REAL,
                    bpm_confidence REAL,
                    detector       TEXT,
                    analyzed_at    TEXT,
                    status         TEXT DEFAULT 'pending',
                    error_message  TEXT,
                    needs_review   INTEGER DEFAULT 0,
                    locked         INTEGER DEFAULT 0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_path    ON tracks(file_path)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status  ON tracks(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_review  ON tracks(needs_review)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_locked  ON tracks(locked)")
            self._migrate(conn)
            conn.commit()

    def _migrate(self, conn):
        """Add columns that may be absent in databases created before this version."""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(tracks)")}
        for col, coldef in [
            ("bpm_dr",       "REAL"),
            ("bpm_es",       "REAL"),
            ("bpm_lb",       "REAL"),
            ("needs_review", "INTEGER DEFAULT 0"),
            ("locked",       "INTEGER DEFAULT 0"),
        ]:
            if col not in existing:
                conn.execute(f"ALTER TABLE tracks ADD COLUMN {col} {coldef}")

    def get_track(self, file_path: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tracks WHERE file_path = ?", (file_path,)).fetchone()
            return dict(row) if row else None

    def upsert_track(self, file_path: str, file_hash: str,
                     bpm: Optional[float], bpm_dr: Optional[float],
                     bpm_es: Optional[float], bpm_lb: Optional[float],
                     confidence: Optional[float], detector: Optional[str],
                     status: str, needs_review: bool = False, error: Optional[str] = None):
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO tracks
                    (file_path, file_hash, bpm, bpm_dr, bpm_es, bpm_lb, bpm_confidence,
                     detector, analyzed_at, status, needs_review, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    file_hash      = excluded.file_hash,
                    bpm            = excluded.bpm,
                    bpm_dr         = excluded.bpm_dr,
                    bpm_es         = excluded.bpm_es,
                    bpm_lb         = excluded.bpm_lb,
                    bpm_confidence = excluded.bpm_confidence,
                    detector       = excluded.detector,
                    analyzed_at    = excluded.analyzed_at,
                    status         = excluded.status,
                    needs_review   = excluded.needs_review,
                    error_message  = excluded.error_message
            """, (file_path, file_hash, bpm, bpm_dr, bpm_es, bpm_lb, confidence,
                  detector, now, status, int(needs_review), error))
            conn.commit()

    def lock_track(self, file_path: str, bpm: Optional[float]):
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO tracks (file_path, bpm, analyzed_at, status, locked)
                VALUES (?, ?, ?, 'done', 1)
                ON CONFLICT(file_path) DO UPDATE SET
                    bpm         = COALESCE(excluded.bpm, bpm),
                    analyzed_at = excluded.analyzed_at,
                    status      = 'done',
                    locked      = 1
            """, (file_path, bpm, now))
            conn.commit()

    def unlock_track(self, file_path: str):
        with self._connect() as conn:
            conn.execute("UPDATE tracks SET locked = 0 WHERE file_path = ?", (file_path,))
            conn.commit()

    def needs_analysis(self, file_path: str, file_hash: str) -> bool:
        track = self.get_track(file_path)
        if not track:
            return True
        if track["locked"]:
            return False
        if track["status"] != "done":
            return True
        return track["file_hash"] != file_hash

    def get_tracks_page(self, q: str, limit: int, offset: int) -> tuple[list[dict], int]:
        with self._connect() as conn:
            if q:
                like = f"%{q}%"
                total = conn.execute(
                    "SELECT COUNT(*) FROM tracks WHERE file_path LIKE ?", (like,)
                ).fetchone()[0]
                rows = conn.execute(
                    "SELECT * FROM tracks WHERE file_path LIKE ? "
                    "ORDER BY analyzed_at DESC LIMIT ? OFFSET ?",
                    (like, limit, offset)
                ).fetchall()
            else:
                total = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
                rows = conn.execute(
                    "SELECT * FROM tracks ORDER BY analyzed_at DESC LIMIT ? OFFSET ?",
                    (limit, offset)
                ).fetchall()
        return [dict(r) for r in rows], total

    def get_suspicious_count(self, conf_threshold: float, bpm_min: float, bpm_max: float) -> int:
        with self._connect() as conn:
            return conn.execute(
                f"SELECT COUNT(*) FROM tracks WHERE {self._SUSPICIOUS_WHERE}",
                (conf_threshold, bpm_min, bpm_max)
            ).fetchone()[0]

    def get_suspicious_page(self, conf_threshold: float, bpm_min: float, bpm_max: float,
                            limit: int, offset: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {self._SUSPICIOUS_COLS} FROM tracks WHERE {self._SUSPICIOUS_WHERE}"
                " ORDER BY bpm_confidence ASC NULLS LAST LIMIT ? OFFSET ?",
                (conf_threshold, bpm_min, bpm_max, limit, offset)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_suspicious(self, conf_threshold: float, bpm_min: float, bpm_max: float) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {self._SUSPICIOUS_COLS} FROM tracks WHERE {self._SUSPICIOUS_WHERE}"
                " ORDER BY bpm_confidence ASC NULLS LAST",
                (conf_threshold, bpm_min, bpm_max)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_reanalysis_queue(self) -> list[str]:
        """Return file paths of unlocked tracks that are candidates for re-analysis."""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT file_path FROM tracks
                WHERE locked = 0 AND (
                    needs_review = 1
                    OR status = 'error'
                    OR detector = 'librosa'
                )
                ORDER BY file_path
            """).fetchall()
            return [r[0] for r in rows]

    def get_stats(self) -> dict:
        with self._connect() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(CASE WHEN status='done'  THEN 1 END) AS done,
                    COUNT(CASE WHEN status='error' THEN 1 END) AS errors,
                    COUNT(CASE WHEN needs_review=1 AND status='done' THEN 1 END) AS needs_review,
                    COUNT(CASE WHEN locked=1       THEN 1 END) AS locked
                FROM tracks
            """).fetchone()
            return dict(row)

    def get_bpm_distribution(self, bucket: int = 5) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT CAST(bpm/? AS INT)*? AS b, COUNT(*) AS n "
                "FROM tracks WHERE status='done' AND bpm IS NOT NULL "
                "GROUP BY b ORDER BY b", (bucket, bucket)
            ).fetchall()
        return [{"bpm": r["b"], "count": r["n"]} for r in rows]

    def get_detector_distribution(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT COALESCE(detector,'unknown') AS d, COUNT(*) AS n "
                "FROM tracks WHERE status='done' GROUP BY d ORDER BY n DESC"
            ).fetchall()
        return [{"detector": r["d"], "count": r["n"]} for r in rows]

    def get_bpm_descriptive(self) -> dict:
        with self._connect() as conn:
            r = conn.execute(
                "SELECT ROUND(AVG(bpm),1) AS avg, MIN(bpm) AS mn, MAX(bpm) AS mx, "
                "COUNT(*) AS n FROM tracks WHERE status='done' AND bpm IS NOT NULL"
            ).fetchone()
            bpms = [row[0] for row in conn.execute(
                "SELECT bpm FROM tracks WHERE status='done' AND bpm IS NOT NULL ORDER BY bpm"
            ).fetchall()]
        if not bpms:
            return {"avg": None, "min": None, "max": None, "median": None, "count": 0}
        mid = len(bpms) // 2
        median = (bpms[mid - 1] + bpms[mid]) / 2 if len(bpms) % 2 == 0 else bpms[mid]
        return {"avg": r["avg"], "min": r["mn"], "max": r["mx"],
                "median": round(median, 1), "count": r["n"]}


# ---------------------------------------------------------------------------
# Notification manager (anti-spam batching)
# ---------------------------------------------------------------------------

class NotificationManager:
    def __init__(self, ntfy_url: str, topic: str, batch_size: int = 10,
                 min_interval: int = 300, notify_review: bool = True):
        self._url = ntfy_url.rstrip("/")
        self._topic = topic
        self._batch_size = batch_size
        self._min_interval = min_interval
        self._notify_review = notify_review
        self._buffer: list[tuple[str, float]] = []
        self._last_sent: float = 0.0
        self._lock = threading.Lock()

    def add(self, file_path: str, bpm: float):
        with self._lock:
            self._buffer.append((Path(file_path).name, bpm))
            elapsed = time.monotonic() - self._last_sent
            if len(self._buffer) >= self._batch_size or elapsed >= self._min_interval:
                self._flush_locked()

    def flush(self):
        with self._lock:
            self._flush_locked()

    def _flush_locked(self):
        if not self._buffer:
            return
        count = len(self._buffer)
        if count == 1:
            name, bpm = self._buffer[0]
            title, body = "BPM Tagged", f"{name}: {bpm:.1f} BPM"
        else:
            title = f"BPM Tagged: {count} tracks"
            lines = [f"• {n}: {b:.1f} BPM" for n, b in self._buffer[:10]]
            if count > 10:
                lines.append(f"  …and {count - 10} more")
            body = "\n".join(lines)
        self._post(title, body, "musical_note")
        self._buffer.clear()
        self._last_sent = time.monotonic()

    def send_summary(self, total: int, tagged: int, errors: int, needs_review: int = 0):
        if tagged == 0 and needs_review == 0:
            return
        review_part = f", {needs_review} need review" if needs_review and self._notify_review else ""
        body = f"Scan complete — {tagged} tagged{review_part}, {errors} errors ({total} total in DB)"
        self._post("BPM Tagger — Scan complete", body, "white_check_mark")

    def send_report(self, suspicious: list[dict]):
        count = len(suspicious)
        lines = []
        for t in suspicious[:15]:
            name = Path(t["file_path"]).name
            bpm = f"{t['bpm']:.1f}" if t["bpm"] is not None else "?"
            dr  = f"{t['bpm_dr']:.1f}" if t["bpm_dr"] is not None else "?"
            es  = f"{t['bpm_es']:.1f}" if t.get("bpm_es") is not None else "?"
            lb  = f"{t['bpm_lb']:.1f}" if t["bpm_lb"] is not None else "?"
            lines.append(f"• {name}: {bpm} BPM [dr={dr} es={es} lb={lb}]")
        if count > 15:
            lines.append(f"  …and {count - 15} more")
        self._post(f"BPM Review Needed: {count} tracks", "\n".join(lines), "warning")

    def _post(self, title: str, body: str, tag: str):
        try:
            resp = requests.post(
                f"{self._url}/{self._topic}",
                data=body.encode(),
                headers={"Title": title, "Priority": "low", "Tags": tag},
                timeout=10,
            )
            resp.raise_for_status()
            log.debug("ntfy notification sent: %s", title)
        except Exception as exc:
            log.warning("ntfy notification failed: %s", exc)


# ---------------------------------------------------------------------------
# Reason builder (shared by logging and CSV)
# ---------------------------------------------------------------------------

def _build_reasons(track: dict, conf_threshold: float, bpm_min: float, bpm_max: float) -> list[str]:
    reasons = []
    if track.get("needs_review"):
        reasons.append(f"detector disagreement (dr={track.get('bpm_dr')} es={track.get('bpm_es')} lb={track.get('bpm_lb')})")
    conf = track.get("bpm_confidence")
    if conf is not None and conf < conf_threshold:
        reasons.append(f"low confidence ({conf:.2f})")
    if track.get("detector") == "librosa":
        reasons.append("fallback detector only")
    bpm = track.get("bpm")
    if bpm is not None and (bpm < bpm_min or bpm > bpm_max):
        reasons.append(f"out of range ({bpm:.1f} BPM)")
    return reasons


# ---------------------------------------------------------------------------
# Navidrome rescan trigger
# ---------------------------------------------------------------------------

def _trigger_navidrome_rescan(config: dict):
    url  = config.get("navidrome_url", "").rstrip("/")
    user = config.get("navidrome_user", "")
    pwd  = config.get("navidrome_pass", "")
    if not (url and user and pwd):
        return
    try:
        salt = secrets.token_hex(6)
        token = hashlib.md5((pwd + salt).encode()).hexdigest()
        resp = requests.get(
            f"{url}/rest/startScan",
            params={"u": user, "t": token, "s": salt, "v": "1.8.0", "c": "bpm-tagger", "f": "json"},
            timeout=10,
        )
        resp.raise_for_status()
        log.info("Navidrome rescan triggered")
    except Exception as exc:
        log.warning("Navidrome rescan request failed: %s", exc)


# ---------------------------------------------------------------------------
# Core tagger
# ---------------------------------------------------------------------------

class BPMTagger:
    def __init__(self, config: dict, progress: Optional[ScanProgress] = None):
        self.config = config
        self.progress = progress or ScanProgress()
        self.db = BPMDatabase(config["db_path"])
        self.notifier: Optional[NotificationManager] = None
        if config.get("ntfy_url") and config.get("ntfy_topic"):
            self.notifier = NotificationManager(
                ntfy_url=config["ntfy_url"],
                topic=config["ntfy_topic"],
                batch_size=int(config.get("ntfy_batch_size", 10)),
                min_interval=int(config.get("ntfy_min_interval", 300)),
                notify_review=config.get("ntfy_notify_review", True),
            )
        self._pause_event = threading.Event()
        self._pause_event.set()   # set = running; clear = paused
        self._stop_event  = threading.Event()

    def process_file(self, file_path: str, force: bool = False) -> dict:
        """Analyze one file. Returns dict with 'status': tagged | skipped | error."""
        if Path(file_path).suffix.lower() not in self.config["extensions"]:
            return {"status": "skipped"}

        file_hash = get_file_hash(file_path)
        if not force and not self.db.needs_analysis(file_path, file_hash):
            log.debug("Skip (unchanged/locked): %s", Path(file_path).name)
            return {"status": "skipped"}

        log.info("Analyzing: %s", Path(file_path).name)
        self.progress.set_file(file_path)
        try:
            result = detect_bpm(file_path, self.config, self.progress)
            bpm = result["bpm"]
            review_flag = " [needs review]" if result["needs_review"] else ""
            log.info("  %.1f BPM (conf %.2f, %s)%s",
                     bpm, result["confidence"], result["detector"], review_flag)

            if self.config["write_tags"]:
                write_bpm_tag(file_path, bpm)

            self.db.upsert_track(
                file_path, file_hash,
                bpm, result["bpm_dr"], result["bpm_es"], result["bpm_lb"],
                result["confidence"], result["detector"],
                "done", needs_review=result["needs_review"],
            )

            if self.notifier:
                self.notifier.add(file_path, bpm)

            self.progress.finish_file(file_path, bpm)
            return {"status": "tagged", **result}
        except Exception as exc:
            log.error("Error analyzing %s: %s", file_path, exc)
            self.db.upsert_track(
                file_path, file_hash,
                None, None, None, None, None, None, "error", error=str(exc),
            )
            self.progress.finish_file(file_path, None)
            return {"status": "error"}

    def _process_files_parallel(self, file_paths: list[str], force: bool) -> dict:
        workers = int(self.config.get("workers", 1))
        counts = {"tagged": 0, "skipped": 0, "errors": 0, "needs_review": 0}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for i in range(0, len(file_paths), workers):
                if self._stop_event.is_set():
                    break
                self._pause_event.wait()   # blocks here while paused
                if self._stop_event.is_set():
                    break
                batch = file_paths[i : i + workers]
                futures = {executor.submit(self.process_file, fp, force): fp for fp in batch}
                batch_counts = self._count_results(futures)
                for k in counts:
                    counts[k] += batch_counts[k]
        return counts

    def _finish_scan(self, counts: dict, label: str):
        if self.notifier:
            self.notifier.flush()
            if counts["tagged"] or counts["needs_review"]:
                stats = self.db.get_stats()
                self.notifier.send_summary(stats["total"], counts["tagged"],
                                           counts["errors"], counts["needs_review"])
        _trigger_navidrome_rescan(self.config)
        log.info("%s done — %d tagged (%d need review), %d skipped, %d errors",
                 label, counts["tagged"], counts["needs_review"],
                 counts["skipped"], counts["errors"])

    def _count_results(self, futures) -> dict:
        """Drain a dict of futures and return tallied counts."""
        tagged = errors = skipped = needs_review_count = 0
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as exc:
                log.error("Worker exception: %s", exc)
                errors += 1
                continue
            if result["status"] == "tagged":
                tagged += 1
                if result.get("needs_review"):
                    needs_review_count += 1
            elif result["status"] == "error":
                errors += 1
            else:
                skipped += 1
        return {"tagged": tagged, "skipped": skipped, "errors": errors,
                "needs_review": needs_review_count}

    def scan_directory(self, force: bool = False) -> dict:
        self._stop_event.clear()
        self._pause_event.set()
        self.progress.set_paused(False)

        audio_files = []
        for root, _, files in os.walk(self.config["music_dir"]):
            for fname in sorted(files):
                if Path(fname).suffix.lower() in self.config["extensions"]:
                    audio_files.append(os.path.join(root, fname))

        self.progress.start(len(audio_files))
        counts = self._process_files_parallel(audio_files, force=force)
        self.progress.finish()
        self._finish_scan(counts, "Scan")
        return counts

    def scan_review(self) -> dict:
        """Re-analyze only flagged, errored, or librosa-only tracks."""
        self._stop_event.clear()
        self._pause_event.set()
        self.progress.set_paused(False)

        queue = self.db.get_reanalysis_queue()
        log.info("scan_review: %d tracks queued for re-analysis", len(queue))

        self.progress.start(len(queue))
        counts = self._process_files_parallel(queue, force=True)
        self.progress.finish()
        self._finish_scan(counts, "scan_review")
        return counts

    def report(self) -> dict:
        conf_thr = self.config["review_confidence_threshold"]
        bpm_min  = self.config["bpm_min"]
        bpm_max  = self.config["bpm_max"]

        suspicious = self.db.get_suspicious(conf_thr, bpm_min, bpm_max)
        log.info("Report: %d suspicious tracks found", len(suspicious))

        report_path = self.config.get("report_path", "/data/review_report.csv")
        os.makedirs(os.path.dirname(os.path.abspath(report_path)), exist_ok=True)

        with open(report_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "file_path", "bpm", "bpm_dr", "bpm_es", "bpm_lb",
                "bpm_confidence", "detector", "needs_review", "reasons",
            ])
            writer.writeheader()
            for t in suspicious:
                reasons = _build_reasons(t, conf_thr, bpm_min, bpm_max)
                bpm_str = f"{t['bpm']:.1f} BPM" if t["bpm"] is not None else "no BPM"
                log.info("  [%s] %s — %s", "; ".join(reasons), Path(t["file_path"]).name, bpm_str)
                writer.writerow({
                    "file_path":      t["file_path"],
                    "bpm":            t["bpm"],
                    "bpm_dr":         t["bpm_dr"],
                    "bpm_es":         t.get("bpm_es"),
                    "bpm_lb":         t["bpm_lb"],
                    "bpm_confidence": t["bpm_confidence"],
                    "detector":       t["detector"],
                    "needs_review":   t["needs_review"],
                    "reasons":        "; ".join(reasons),
                })

        log.info("Report written to %s", report_path)
        if self.notifier and suspicious:
            self.notifier.send_report(suspicious)

        return {"suspicious": len(suspicious), "report_path": report_path}

    def watch(self):
        log.info("Watching %s for new/updated files...", self.config["music_dir"])

        class _Handler(FileSystemEventHandler):
            def __init__(self, tagger: "BPMTagger"):
                self._tagger = tagger
                self._pending: dict[str, float] = {}
                self._lock = threading.Lock()

            def _schedule(self, path: str):
                with self._lock:
                    self._pending[path] = time.monotonic() + 10

            def on_created(self, event):
                if not event.is_directory:
                    self._schedule(event.src_path)

            def on_modified(self, event):
                if not event.is_directory:
                    self._schedule(event.src_path)

            def on_moved(self, event):
                if not event.is_directory:
                    with self._lock:
                        self._pending.pop(event.src_path, None)
                        self._pending[event.dest_path] = time.monotonic() + 10

            def drain_pending(self):
                while not self._tagger._stop_event.is_set():
                    time.sleep(2)
                    try:
                        with self._lock:
                            now = time.monotonic()
                            ready = [p for p, deadline in self._pending.items() if deadline <= now]
                            for p in ready:
                                del self._pending[p]
                        for path in ready:
                            if self._tagger._stop_event.is_set():
                                break
                            self._tagger._pause_event.wait()
                            self._tagger.process_file(path, force=True)
                    except Exception as exc:
                        log.error("drain_pending error: %s", exc)

        handler = _Handler(self)
        threading.Thread(target=handler.drain_pending, daemon=True).start()

        observer = Observer()
        observer.schedule(handler, self.config["music_dir"], recursive=True)
        observer.start()

        try:
            while not self._stop_event.is_set():
                time.sleep(60)
                if self.notifier:
                    self.notifier.flush()
        except KeyboardInterrupt:
            pass
        log.info("Shutting down watcher...")
        observer.stop()
        observer.join()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=getattr(logging, level, logging.INFO),
                        format="%(asctime)s %(levelname)-8s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")

    raw_ext = os.environ.get("AUDIO_EXTENSIONS", ",".join(AUDIO_EXTENSIONS))
    extensions = {e.strip().lower() for e in raw_ext.split(",") if e.strip()}

    config = {
        "music_dir":                  os.environ.get("MUSIC_DIR", "/music"),
        "db_path":                    os.environ.get("DB_PATH", "/data/bpm_tagger.db"),
        "ntfy_url":                   os.environ.get("NTFY_URL", ""),
        "ntfy_topic":                 os.environ.get("NTFY_TOPIC", ""),
        "ntfy_batch_size":            int(os.environ.get("NTFY_BATCH_SIZE", "10")),
        "ntfy_min_interval":          int(os.environ.get("NTFY_MIN_INTERVAL", "300")),
        "ntfy_notify_review":         os.environ.get("NTFY_NOTIFY_REVIEW", "true").lower() == "true",
        "write_tags":                 os.environ.get("WRITE_TAGS", "true").lower() == "true",
        "extensions":                 extensions,
        "bpm_min":                    float(os.environ.get("BPM_MIN", "60")),
        "bpm_max":                    float(os.environ.get("BPM_MAX", "200")),
        "octave_correction":          os.environ.get("OCTAVE_CORRECTION", "true").lower() == "true",
        "multi_segment":              os.environ.get("MULTI_SEGMENT", "true").lower() == "true",
        "multi_segment_count":        int(os.environ.get("MULTI_SEGMENT_COUNT", "3")),
        "segment_duration":           float(os.environ.get("SEGMENT_DURATION", "45")),
        "review_confidence_threshold":float(os.environ.get("REVIEW_CONFIDENCE_THRESHOLD", "0.4")),
        "review_disagree_threshold":  float(os.environ.get("REVIEW_DISAGREE_THRESHOLD", "15")),
        "use_deeprhythm":             os.environ.get("USE_DEEPRHYTHM", "true").lower() == "true",
        "use_essentia":               os.environ.get("USE_ESSENTIA", "true").lower() == "true",
        "report_path":                os.environ.get("REPORT_PATH", "/data/review_report.csv"),
        "enable_ui":                  os.environ.get("ENABLE_UI", "false").lower() == "true",
        "ui_port":                    int(os.environ.get("UI_PORT", "5000")),
        "ui_password":                os.environ.get("UI_PASSWORD", ""),
        "ui_secret_key":              os.environ.get("UI_SECRET_KEY", ""),
        "ui_session_hours":           int(os.environ.get("UI_SESSION_HOURS", "24")),
        "ui_max_login_attempts":      int(os.environ.get("UI_MAX_LOGIN_ATTEMPTS", "5")),
        "ui_lockout_seconds":         int(os.environ.get("UI_LOCKOUT_SECONDS", "300")),
        "workers":                    int(os.environ.get("WORKERS", "1")),
        "navidrome_url":              os.environ.get("NAVIDROME_URL", ""),
        "navidrome_user":             os.environ.get("NAVIDROME_USER", ""),
        "navidrome_pass":             os.environ.get("NAVIDROME_PASS", ""),
    }

    config = load_settings_override(config)

    mode = os.environ.get("MODE", "scan_unscanned").lower()
    # settings file may override mode
    mode = config.get("mode", mode)
    scan_on_start = os.environ.get("SCAN_ON_START", "true").lower() == "true"

    log.info("BPM Tagger starting — mode=%s, music_dir=%s", mode, config["music_dir"])

    progress = ScanProgress()
    tagger = BPMTagger(config, progress)

    if config["enable_ui"]:
        import web_ui
        threading.Thread(target=web_ui.start, args=(config, progress, tagger), daemon=True).start()

    if mode == "scan_all":
        tagger.scan_directory(force=True)

    elif mode == "scan_unscanned":
        tagger.scan_directory(force=False)

    elif mode == "watch":
        if scan_on_start:
            tagger.scan_directory(force=False)
        tagger.watch()

    elif mode == "report":
        result = tagger.report()
        log.info("Report complete — %d suspicious tracks → %s",
                 result["suspicious"], result["report_path"])

    elif mode == "lock":
        file_path = os.environ.get("LOCK_FILE", "").strip()
        if not file_path:
            log.error("LOCK_FILE env var is required for MODE=lock")
            sys.exit(1)
        lock_bpm_raw = os.environ.get("LOCK_BPM", "").strip()
        lock_bpm = float(lock_bpm_raw) if lock_bpm_raw else None
        tagger.db.lock_track(file_path, lock_bpm)
        if lock_bpm is not None and config["write_tags"]:
            write_bpm_tag(file_path, lock_bpm)
        bpm_msg = f" at {lock_bpm:.1f} BPM" if lock_bpm is not None else " (keeping existing BPM)"
        log.info("Locked %s%s", file_path, bpm_msg)

    elif mode == "unlock":
        file_path = os.environ.get("UNLOCK_FILE", "").strip()
        if not file_path:
            log.error("UNLOCK_FILE env var is required for MODE=unlock")
            sys.exit(1)
        tagger.db.unlock_track(file_path)
        log.info("Unlocked %s — will be re-analyzed on next scan", file_path)

    elif mode == "scan_review":
        tagger.scan_review()

    else:
        log.error("Unknown MODE '%s'. Use: scan_all, scan_unscanned, scan_review, watch, report, lock, unlock", mode)
        sys.exit(1)

    # For non-blocking modes, keep the process alive so the UI thread stays up.
    if config["enable_ui"] and mode != "watch":
        log.info("Work complete. UI still available — press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            log.info("Shutting down.")


if __name__ == "__main__":
    main()
