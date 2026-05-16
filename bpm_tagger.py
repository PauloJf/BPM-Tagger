#!/usr/bin/env python3
"""BPM Tagger for Navidrome — detects BPM, writes tags, tracks results in SQLite."""

import csv
import logging
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import librosa
import mutagen
import numpy as np
import requests
import soundfile as sf
from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError, TBPM
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".mp3", ".flac", ".ogg", ".m4a", ".aac", ".wav", ".opus", ".wv"}

# ---------------------------------------------------------------------------
# Low-level BPM helpers
# ---------------------------------------------------------------------------

def _detect_bpm_deeprhythm(file_path: str) -> float:
    from deeprhythm import BPMPredictor
    predictor = BPMPredictor()
    return round(float(predictor.predict(file_path)), 1)


def _librosa_window(file_path: str, offset: float, duration: float) -> tuple[float, float]:
    y, sr = librosa.load(file_path, offset=offset, duration=duration, sr=None, mono=True)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, aggregate=np.median)
    candidates = librosa.feature.rhythm.tempo(onset_envelope=onset_env, sr=sr, aggregate=None)
    bpm = float(np.median(candidates)) if len(candidates) > 0 else 0.0
    _, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, trim=False)
    if len(beats) > 2:
        intervals = np.diff(librosa.frames_to_time(beats, sr=sr))
        conf = float(np.clip(1.0 - np.std(intervals) / (np.mean(intervals) + 1e-9), 0.0, 1.0))
    else:
        conf = 0.0
    return round(bpm, 1), conf


def _detect_bpm_librosa_multiseg(file_path: str, n_segments: int, seg_duration: float) -> tuple[float, float]:
    try:
        info = sf.info(file_path)
        total = info.duration
    except Exception:
        total = 0.0

    if total < seg_duration * 1.5:
        return _librosa_window(file_path, 0.0, 180.0)

    offsets = []
    for i in range(n_segments):
        center = total * (i + 1) / (n_segments + 1)
        o = max(0.0, min(center - seg_duration / 2, total - seg_duration))
        offsets.append(o)

    bpms, confs = [], []
    for o in offsets:
        b, c = _librosa_window(file_path, o, seg_duration)
        bpms.append(b)
        confs.append(c)

    return round(float(np.median(bpms)), 1), float(np.median(confs))


# ---------------------------------------------------------------------------
# Reconciliation + plausibility
# ---------------------------------------------------------------------------

def _reconcile(bpm_dr: float, bpm_lb: float, config: dict) -> tuple[float, bool]:
    """Return (final_bpm, needs_review). Prefers deeprhythm; corrects octave errors."""
    ratio = max(bpm_dr, bpm_lb) / (min(bpm_dr, bpm_lb) + 1e-9)
    is_octave = 1.9 < ratio < 2.1

    if is_octave and config["octave_correction"]:
        bpm_min, bpm_max = config["bpm_min"], config["bpm_max"]
        for candidate in (bpm_dr, bpm_lb):
            if bpm_min <= candidate <= bpm_max:
                return candidate, False
        center = (bpm_min + bpm_max) / 2
        return min(bpm_dr, bpm_lb, key=lambda x: abs(x - center)), False

    needs_review = abs(bpm_dr - bpm_lb) > config["review_disagree_threshold"]
    return bpm_dr, needs_review


def _normalize_bpm(bpm: float, bpm_min: float, bpm_max: float) -> float:
    """Halve/double until BPM is inside [bpm_min, bpm_max]."""
    while bpm > 0 and bpm < bpm_min:
        bpm *= 2
    while bpm > bpm_max:
        bpm /= 2
    return round(bpm, 1)


# ---------------------------------------------------------------------------
# Public BPM detection entry point
# ---------------------------------------------------------------------------

def detect_bpm(file_path: str, config: dict) -> dict:
    """
    Return dict with keys: bpm, bpm_dr, bpm_lb, confidence, detector, needs_review.
    Always runs both detectors; reconciles octave errors; normalizes to BPM range.
    """
    n_seg = int(config.get("multi_segment_count", 3))
    seg_dur = float(config.get("segment_duration", 45))

    # deeprhythm
    bpm_dr: Optional[float] = None
    try:
        bpm_dr = _detect_bpm_deeprhythm(file_path)
    except Exception as exc:
        log.debug("deeprhythm failed for %s: %s", Path(file_path).name, exc)

    # librosa (always runs)
    if config.get("multi_segment", True):
        bpm_lb, conf_lb = _detect_bpm_librosa_multiseg(file_path, n_seg, seg_dur)
    else:
        bpm_lb, conf_lb = _librosa_window(file_path, 0.0, 180.0)

    # decide final BPM
    if bpm_dr is None:
        bpm_final = bpm_lb
        detector = "librosa"
        needs_review = False
    else:
        bpm_final, needs_review = _reconcile(bpm_dr, bpm_lb, config)
        detector = "deeprhythm+librosa"

    bpm_final = _normalize_bpm(bpm_final, config["bpm_min"], config["bpm_max"])

    return {
        "bpm":          bpm_final,
        "bpm_dr":       bpm_dr,
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tracks (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path      TEXT UNIQUE NOT NULL,
                    file_hash      TEXT,
                    bpm            REAL,
                    bpm_dr         REAL,
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
        additions = [
            ("bpm_dr",       "REAL"),
            ("bpm_lb",       "REAL"),
            ("needs_review", "INTEGER DEFAULT 0"),
            ("locked",       "INTEGER DEFAULT 0"),
        ]
        for col, coldef in additions:
            if col not in existing:
                conn.execute(f"ALTER TABLE tracks ADD COLUMN {col} {coldef}")

    def get_track(self, file_path: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tracks WHERE file_path = ?", (file_path,)).fetchone()
            return dict(row) if row else None

    def upsert_track(self, file_path: str, file_hash: str,
                     bpm: Optional[float], bpm_dr: Optional[float], bpm_lb: Optional[float],
                     confidence: Optional[float], detector: Optional[str],
                     status: str, needs_review: bool = False, error: Optional[str] = None):
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO tracks
                    (file_path, file_hash, bpm, bpm_dr, bpm_lb, bpm_confidence,
                     detector, analyzed_at, status, needs_review, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    file_hash      = excluded.file_hash,
                    bpm            = excluded.bpm,
                    bpm_dr         = excluded.bpm_dr,
                    bpm_lb         = excluded.bpm_lb,
                    bpm_confidence = excluded.bpm_confidence,
                    detector       = excluded.detector,
                    analyzed_at    = excluded.analyzed_at,
                    status         = excluded.status,
                    needs_review   = excluded.needs_review,
                    error_message  = excluded.error_message
            """, (file_path, file_hash, bpm, bpm_dr, bpm_lb, confidence,
                  detector, now, status, int(needs_review), error))
            conn.commit()

    def lock_track(self, file_path: str, bpm: Optional[float]):
        now = datetime.now(timezone.utc).isoformat()
        existing = self.get_track(file_path)
        final_bpm = bpm if bpm is not None else (existing["bpm"] if existing else None)
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO tracks (file_path, bpm, analyzed_at, status, locked)
                VALUES (?, ?, ?, 'done', 1)
                ON CONFLICT(file_path) DO UPDATE SET
                    bpm         = COALESCE(excluded.bpm, bpm),
                    analyzed_at = excluded.analyzed_at,
                    status      = 'done',
                    locked      = 1
            """, (file_path, final_bpm, now))
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

    def get_suspicious(self, conf_threshold: float, bpm_min: float, bpm_max: float) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT file_path, bpm, bpm_dr, bpm_lb, bpm_confidence, detector, needs_review
                FROM tracks
                WHERE status = 'done' AND locked = 0 AND (
                    needs_review = 1
                    OR (bpm_confidence IS NOT NULL AND bpm_confidence < ?)
                    OR detector = 'librosa'
                    OR (bpm IS NOT NULL AND (bpm < ? OR bpm > ?))
                )
                ORDER BY bpm_confidence ASC NULLS LAST
            """, (conf_threshold, bpm_min, bpm_max)).fetchall()
            return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        with self._connect() as conn:
            total        = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
            done         = conn.execute("SELECT COUNT(*) FROM tracks WHERE status='done'").fetchone()[0]
            errors       = conn.execute("SELECT COUNT(*) FROM tracks WHERE status='error'").fetchone()[0]
            needs_review = conn.execute("SELECT COUNT(*) FROM tracks WHERE needs_review=1 AND status='done'").fetchone()[0]
            locked       = conn.execute("SELECT COUNT(*) FROM tracks WHERE locked=1").fetchone()[0]
            return {"total": total, "done": done, "errors": errors,
                    "needs_review": needs_review, "locked": locked}


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
            title = "BPM Tagged"
            body = f"{name}: {bpm:.1f} BPM"
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
        title = f"BPM Review Needed: {count} tracks"
        lines = []
        for t in suspicious[:15]:
            name = Path(t["file_path"]).name
            bpm = f"{t['bpm']:.1f}" if t["bpm"] is not None else "?"
            dr = f"{t['bpm_dr']:.1f}" if t["bpm_dr"] is not None else "?"
            lb = f"{t['bpm_lb']:.1f}" if t["bpm_lb"] is not None else "?"
            lines.append(f"• {name}: {bpm} BPM [dr={dr} lb={lb}]")
        if count > 15:
            lines.append(f"  …and {count - 15} more")
        body = "\n".join(lines)
        self._post(title, body, "warning")

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
        dr = track.get("bpm_dr")
        lb = track.get("bpm_lb")
        reasons.append(f"detector disagreement (dr={dr} lb={lb})")
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
# Core tagger
# ---------------------------------------------------------------------------

class BPMTagger:
    def __init__(self, config: dict):
        self.music_dir = config["music_dir"]
        self.write_tags = config.get("write_tags", True)
        self.extensions = {e.lower() for e in config.get("extensions", AUDIO_EXTENSIONS)}
        self.config = config
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

    def process_file(self, file_path: str, force: bool = False) -> Optional[dict]:
        """Analyze one file. Returns result dict if newly analyzed, None if skipped/locked."""
        if Path(file_path).suffix.lower() not in self.extensions:
            return None

        file_hash = get_file_hash(file_path)
        if not force and not self.db.needs_analysis(file_path, file_hash):
            log.debug("Skip (unchanged/locked): %s", Path(file_path).name)
            return None

        log.info("Analyzing: %s", Path(file_path).name)
        try:
            result = detect_bpm(file_path, self.config)
            bpm = result["bpm"]
            review_flag = " [needs review]" if result["needs_review"] else ""
            log.info("  %.1f BPM (conf %.2f, %s)%s",
                     bpm, result["confidence"], result["detector"], review_flag)

            if self.write_tags:
                write_bpm_tag(file_path, bpm)

            self.db.upsert_track(
                file_path, file_hash,
                bpm, result["bpm_dr"], result["bpm_lb"],
                result["confidence"], result["detector"],
                "done", needs_review=result["needs_review"],
            )

            if self.notifier:
                self.notifier.add(file_path, bpm)

            return result
        except Exception as exc:
            log.error("Error analyzing %s: %s", file_path, exc)
            self.db.upsert_track(
                file_path, file_hash,
                None, None, None, None, None,
                "error", error=str(exc),
            )
            return None

    def scan_directory(self, force: bool = False) -> dict:
        tagged = errors = skipped = needs_review_count = 0
        for root, _, files in os.walk(self.music_dir):
            for fname in sorted(files):
                if Path(fname).suffix.lower() not in self.extensions:
                    continue
                file_path = os.path.join(root, fname)
                result = self.process_file(file_path, force=force)
                if result is not None:
                    tagged += 1
                    if result["needs_review"]:
                        needs_review_count += 1
                else:
                    track = self.db.get_track(file_path)
                    if track and track["status"] == "error":
                        errors += 1
                    else:
                        skipped += 1

        if self.notifier:
            self.notifier.flush()
            stats = self.db.get_stats()
            self.notifier.send_summary(stats["total"], tagged, errors, needs_review_count)

        log.info("Scan done — %d tagged (%d need review), %d skipped, %d errors",
                 tagged, needs_review_count, skipped, errors)
        return {"tagged": tagged, "skipped": skipped, "errors": errors,
                "needs_review": needs_review_count}

    def report(self) -> dict:
        conf_thr = self.config["review_confidence_threshold"]
        bpm_min  = self.config["bpm_min"]
        bpm_max  = self.config["bpm_max"]

        suspicious = self.db.get_suspicious(conf_thr, bpm_min, bpm_max)
        log.info("Report: %d suspicious tracks found", len(suspicious))

        for t in suspicious:
            reasons = _build_reasons(t, conf_thr, bpm_min, bpm_max)
            bpm_str = f"{t['bpm']:.1f} BPM" if t["bpm"] is not None else "no BPM"
            log.info("  [%s] %s — %s", "; ".join(reasons), Path(t["file_path"]).name, bpm_str)

        # CSV export
        report_path = self.config.get("report_path", "/data/review_report.csv")
        os.makedirs(os.path.dirname(os.path.abspath(report_path)), exist_ok=True)
        with open(report_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "file_path", "bpm", "bpm_dr", "bpm_lb",
                "bpm_confidence", "detector", "needs_review", "reasons",
            ])
            writer.writeheader()
            for t in suspicious:
                reasons = _build_reasons(t, conf_thr, bpm_min, bpm_max)
                writer.writerow({
                    "file_path":      t["file_path"],
                    "bpm":            t["bpm"],
                    "bpm_dr":         t["bpm_dr"],
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
        log.info("Watching %s for new/updated files...", self.music_dir)

        tagger = self

        class _Handler(FileSystemEventHandler):
            def __init__(self):
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
                    self._schedule(event.dest_path)

            def drain_pending(self):
                while True:
                    time.sleep(2)
                    with self._lock:
                        now = time.monotonic()
                        ready = [p for p, t in self._pending.items() if t <= now]
                        for p in ready:
                            del self._pending[p]
                    for path in ready:
                        tagger.process_file(path, force=True)

        handler = _Handler()
        threading.Thread(target=handler.drain_pending, daemon=True).start()

        observer = Observer()
        observer.schedule(handler, self.music_dir, recursive=True)
        observer.start()

        try:
            while True:
                time.sleep(60)
                if self.notifier:
                    self.notifier.flush()
        except KeyboardInterrupt:
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
        "report_path":                os.environ.get("REPORT_PATH", "/data/review_report.csv"),
    }

    mode = os.environ.get("MODE", "scan_unscanned").lower()
    scan_on_start = os.environ.get("SCAN_ON_START", "true").lower() == "true"

    log.info("BPM Tagger starting — mode=%s, music_dir=%s", mode, config["music_dir"])

    tagger = BPMTagger(config)

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

    else:
        log.error("Unknown MODE '%s'. Use: scan_all, scan_unscanned, watch, report, lock, unlock", mode)
        sys.exit(1)


if __name__ == "__main__":
    main()
