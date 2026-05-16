#!/usr/bin/env python3
"""BPM Tagger for Navidrome — detects BPM, writes tags, tracks results in SQLite."""

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
from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError, TBPM
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".mp3", ".flac", ".ogg", ".m4a", ".aac", ".wav", ".opus", ".wv"}

# ---------------------------------------------------------------------------
# BPM detection
# ---------------------------------------------------------------------------

def _detect_bpm_deeprhythm(file_path: str) -> tuple[float, float]:
    from deeprhythm import BPMPredictor
    predictor = BPMPredictor()
    bpm = predictor.predict(file_path)
    return round(float(bpm), 1), 1.0


def _detect_bpm_librosa(file_path: str) -> tuple[float, float]:
    y, sr = librosa.load(file_path, sr=None, mono=True, duration=180)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, aggregate=np.median)
    tempo_candidates = librosa.feature.rhythm.tempo(onset_envelope=onset_env, sr=sr, aggregate=None)
    bpm = float(np.median(tempo_candidates)) if len(tempo_candidates) > 0 else 0.0

    _, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, trim=False)
    if len(beats) > 2:
        intervals = np.diff(librosa.frames_to_time(beats, sr=sr))
        confidence = float(np.clip(1.0 - np.std(intervals) / (np.mean(intervals) + 1e-9), 0.0, 1.0))
    else:
        confidence = 0.0

    return round(bpm, 1), confidence


def detect_bpm(file_path: str) -> tuple[float, float, str]:
    """Return (bpm, confidence, detector_name). Tries deeprhythm, falls back to librosa."""
    try:
        bpm, conf = _detect_bpm_deeprhythm(file_path)
        return bpm, conf, "deeprhythm"
    except Exception as exc:
        log.debug("deeprhythm failed (%s), falling back to librosa", exc)

    bpm, conf = _detect_bpm_librosa(file_path)
    return bpm, conf, "librosa"


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
                    bpm_confidence REAL,
                    detector       TEXT,
                    analyzed_at    TEXT,
                    status         TEXT DEFAULT 'pending',
                    error_message  TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_path   ON tracks(file_path)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON tracks(status)")
            conn.commit()

    def get_track(self, file_path: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tracks WHERE file_path = ?", (file_path,)).fetchone()
            return dict(row) if row else None

    def upsert_track(self, file_path: str, file_hash: str, bpm: Optional[float],
                     confidence: Optional[float], detector: Optional[str],
                     status: str, error: Optional[str] = None):
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO tracks
                    (file_path, file_hash, bpm, bpm_confidence, detector, analyzed_at, status, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    file_hash      = excluded.file_hash,
                    bpm            = excluded.bpm,
                    bpm_confidence = excluded.bpm_confidence,
                    detector       = excluded.detector,
                    analyzed_at    = excluded.analyzed_at,
                    status         = excluded.status,
                    error_message  = excluded.error_message
            """, (file_path, file_hash, bpm, confidence, detector, now, status, error))
            conn.commit()

    def needs_analysis(self, file_path: str, file_hash: str) -> bool:
        track = self.get_track(file_path)
        if not track:
            return True
        if track["status"] != "done":
            return True
        return track["file_hash"] != file_hash

    def get_stats(self) -> dict:
        with self._connect() as conn:
            total  = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
            done   = conn.execute("SELECT COUNT(*) FROM tracks WHERE status='done'").fetchone()[0]
            errors = conn.execute("SELECT COUNT(*) FROM tracks WHERE status='error'").fetchone()[0]
            return {"total": total, "done": done, "errors": errors}


# ---------------------------------------------------------------------------
# Notification manager (anti-spam batching)
# ---------------------------------------------------------------------------

class NotificationManager:
    def __init__(self, ntfy_url: str, topic: str, batch_size: int = 10, min_interval: int = 300):
        self._url = ntfy_url.rstrip("/")
        self._topic = topic
        self._batch_size = batch_size
        self._min_interval = min_interval
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

    def send_summary(self, total: int, tagged: int, errors: int):
        if tagged == 0:
            return
        body = f"Scan complete — {tagged} tagged, {errors} errors ({total} total in DB)"
        self._post("BPM Tagger — Scan complete", body, "white_check_mark")

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
# Core tagger
# ---------------------------------------------------------------------------

class BPMTagger:
    def __init__(self, config: dict):
        self.music_dir = config["music_dir"]
        self.write_tags = config.get("write_tags", True)
        self.extensions = {e.lower() for e in config.get("extensions", AUDIO_EXTENSIONS)}
        self.db = BPMDatabase(config["db_path"])
        self.notifier: Optional[NotificationManager] = None
        if config.get("ntfy_url") and config.get("ntfy_topic"):
            self.notifier = NotificationManager(
                ntfy_url=config["ntfy_url"],
                topic=config["ntfy_topic"],
                batch_size=int(config.get("ntfy_batch_size", 10)),
                min_interval=int(config.get("ntfy_min_interval", 300)),
            )

    def process_file(self, file_path: str, force: bool = False) -> Optional[float]:
        """Analyze one file. Returns BPM if newly analyzed, None if skipped."""
        if Path(file_path).suffix.lower() not in self.extensions:
            return None

        file_hash = get_file_hash(file_path)
        if not force and not self.db.needs_analysis(file_path, file_hash):
            log.debug("Skip (unchanged): %s", Path(file_path).name)
            return None

        log.info("Analyzing: %s", Path(file_path).name)
        try:
            bpm, confidence, detector = detect_bpm(file_path)
            log.info("  %.1f BPM (confidence %.2f, detector: %s)", bpm, confidence, detector)

            if self.write_tags:
                write_bpm_tag(file_path, bpm)

            self.db.upsert_track(file_path, file_hash, bpm, confidence, detector, "done")

            if self.notifier:
                self.notifier.add(file_path, bpm)

            return bpm
        except Exception as exc:
            log.error("Error analyzing %s: %s", file_path, exc)
            self.db.upsert_track(file_path, file_hash, None, None, None, "error", str(exc))
            return None

    def scan_directory(self, force: bool = False) -> dict:
        tagged = errors = skipped = 0
        for root, _, files in os.walk(self.music_dir):
            for fname in sorted(files):
                if Path(fname).suffix.lower() not in self.extensions:
                    continue
                file_path = os.path.join(root, fname)
                result = self.process_file(file_path, force=force)
                if result is not None:
                    tagged += 1
                else:
                    track = self.db.get_track(file_path)
                    if track and track["status"] == "error":
                        errors += 1
                    else:
                        skipped += 1

        if self.notifier:
            self.notifier.flush()
            stats = self.db.get_stats()
            self.notifier.send_summary(stats["total"], tagged, errors)

        log.info("Scan done — %d tagged, %d skipped, %d errors", tagged, skipped, errors)
        return {"tagged": tagged, "skipped": skipped, "errors": errors}

    def watch(self):
        log.info("Watching %s for new/updated files...", self.music_dir)

        tagger = self

        class _Handler(FileSystemEventHandler):
            def __init__(self):
                self._pending: dict[str, float] = {}
                self._lock = threading.Lock()

            def _schedule(self, path: str):
                with self._lock:
                    self._pending[path] = time.monotonic() + 10  # 10s debounce

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
        "music_dir":        os.environ.get("MUSIC_DIR", "/music"),
        "db_path":          os.environ.get("DB_PATH", "/data/bpm_tagger.db"),
        "ntfy_url":         os.environ.get("NTFY_URL", ""),
        "ntfy_topic":       os.environ.get("NTFY_TOPIC", ""),
        "ntfy_batch_size":  int(os.environ.get("NTFY_BATCH_SIZE", "10")),
        "ntfy_min_interval":int(os.environ.get("NTFY_MIN_INTERVAL", "300")),
        "write_tags":       os.environ.get("WRITE_TAGS", "true").lower() == "true",
        "extensions":       extensions,
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
    else:
        log.error("Unknown MODE '%s'. Use: scan_all, scan_unscanned, watch", mode)
        sys.exit(1)


if __name__ == "__main__":
    main()
