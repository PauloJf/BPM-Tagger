"""Filesystem watch handler — debounces events and drains them through the tagger."""

import logging
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler

from ..bpm.detectors import _local
from ..integrations.navidrome import _trigger_navidrome_rescan

log = logging.getLogger(__name__)


class WatchHandler(FileSystemEventHandler):
    def __init__(self, tagger):
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

    def on_deleted(self, event):
        if not event.is_directory:
            fp = event.src_path
            if Path(fp).suffix.lower() in self._tagger.config["extensions"]:
                with self._lock:
                    self._pending.pop(fp, None)
                self._tagger.db.mark_deleted(fp)
                log.info("File deleted — marked as deleted in DB: %s", Path(fp).name)

    def on_moved(self, event):
        if not event.is_directory:
            with self._lock:
                self._pending.pop(event.src_path, None)
                self._pending[event.dest_path] = time.monotonic() + 10
            # The source path no longer exists — mark it deleted
            if Path(event.src_path).suffix.lower() in self._tagger.config["extensions"]:
                self._tagger.db.mark_deleted(event.src_path)
                log.info("File moved — old path marked as deleted: %s",
                         Path(event.src_path).name)

    def drain_pending(self):
        idle_release_secs = 300  # release model after 5 min with no new files
        last_work_time = 0.0
        last_navidrome = 0.0
        any_tagged = False
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
                    result = self._tagger.process_file(path, force=False)
                    if result.get("status") == "tagged":
                        any_tagged = True
                if ready:
                    last_work_time = time.monotonic()
                else:
                    # Queue just drained — trigger Navidrome if files were tagged
                    if any_tagged and not self._pending:
                        now_t = time.monotonic()
                        if now_t - last_navidrome > 60:
                            _trigger_navidrome_rescan(self._tagger.config)
                            last_navidrome = now_t
                        any_tagged = False
                    if (last_work_time > 0
                      and time.monotonic() - last_work_time > idle_release_secs
                      and hasattr(_local, "predictor")):
                        del _local.predictor
                        import gc; gc.collect()
                        log.info("DeepRhythm model released after %d min idle",
                                 idle_release_secs // 60)
                        last_work_time = 0.0
            except Exception as exc:
                log.error("drain_pending error: %s", exc)
