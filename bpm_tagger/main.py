"""Entry point — builds config, applies overrides, and dispatches on MODE."""

import logging
import os
import sys
import threading
import time

from .bpm.pipeline import ScanProgress
from .bpm.tags import write_bpm_tag
from .config import build_config, load_settings_override
from .scan.scanner import BPMTagger

log = logging.getLogger(__name__)


def main():
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=getattr(logging, level, logging.INFO),
                        format="%(asctime)s %(levelname)-8s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")

    config = build_config()
    config = load_settings_override(config)

    if (os.environ.get("USE_DEEPRHYTHM", "false").lower() == "true"
            and os.environ.get("WITH_DEEPRHYTHM", "false").lower() != "true"):
        log.warning(
            "USE_DEEPRHYTHM=true has no effect: this is the slim image built without "
            "PyTorch. Use the ':full' image tag to enable the DeepRhythm detector."
        )

    mode = os.environ.get("MODE", "watch").lower()
    # settings file may override mode
    mode = config.get("mode", mode)

    log.info("BPM Tagger starting — mode=%s, music_dir=%s", mode, config["music_dir"])

    progress = ScanProgress()
    tagger = BPMTagger(config, progress)

    grabber = None
    if config.get("grabber_enabled"):
        from .grabber.sync_engine import GrabberService
        grabber = GrabberService(config, tagger.db, tagger, tagger.notifier)
        tagger.grabber = grabber
        log.info("Grabber enabled — Spotify configured=%s, connected=%s",
                 grabber.client.is_configured(), grabber.client.is_connected())

    if config["enable_ui"]:
        from .web.app import start as web_start
        threading.Thread(target=web_start, args=(config, progress, tagger), daemon=True).start()

    if os.environ.get("REFRESH_HASHES", "false").lower() == "true":
        log.info("REFRESH_HASHES: updating stored hashes for all done/locked tracks…")
        updated, missing = tagger.db.refresh_hashes()
        log.info("REFRESH_HASHES: %d updated, %d files not found on disk", updated, missing)

    if mode == "scan_all":
        tagger.scan_directory(force=True)

    elif mode == "scan_unscanned":
        tagger.scan_directory(force=False)

    elif mode == "watch":
        tagger.scan_directory(force=False)
        if grabber:
            grabber.start_background()
        tagger.watch()

    elif mode == "watch_all":
        tagger.scan_directory(force=True)
        if grabber:
            grabber.start_background()
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
            write_bpm_tag(file_path, lock_bpm, config.get("preserve_mtime", True))
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
        log.error("Unknown MODE '%s'. Use: scan_all, scan_unscanned, scan_review, watch, watch_all, report, lock, unlock", mode)
        sys.exit(1)

    # For non-blocking modes, keep the process alive so the UI thread stays up.
    if config["enable_ui"] and mode not in ("watch", "watch_all"):
        log.info("Work complete. UI still available — press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            log.info("Shutting down.")
