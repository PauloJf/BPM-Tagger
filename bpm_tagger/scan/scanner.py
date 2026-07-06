"""Core tagger — scan phases, parallel processing, watch loop, and review report."""

import csv
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from watchdog.observers import Observer

from ..bpm.pipeline import ScanProgress, detect_bpm
from ..bpm.tags import get_file_hash, write_bpm_tag
from ..bpm.waveform import compute_waveform_peaks
from ..db import BPMDatabase
from ..integrations.navidrome import _trigger_navidrome_rescan
from ..notify.ntfy import NotificationManager
from .watcher import WatchHandler

log = logging.getLogger(__name__)


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
        self.grabber = None       # GrabberService, set in main() when enabled

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
                # Re-read hash after tagging so the stored value matches the
                # post-tag file state; otherwise the next scan sees a mismatch
                # and re-analyzes an already-tagged file.
                file_hash = get_file_hash(file_path)

            # Compute waveform while the file is still warm in the OS page cache
            waveform_peaks = compute_waveform_peaks(file_path)

            self.db.upsert_track(
                file_path, file_hash,
                bpm, result["bpm_dr"], result["bpm_es"], result["bpm_lb"],
                result["confidence"], result["detector"],
                "done", needs_review=result["needs_review"],
                waveform_peaks=waveform_peaks,
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
        # Worker threads have exited; their thread-local model instances are eligible
        # for collection. Run GC explicitly to reclaim torch weights promptly.
        import gc; gc.collect()

    def _count_results(self, futures) -> dict:
        """Drain a dict of futures and return tallied counts."""
        tagged = errors = skipped = needs_review_count = 0
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as exc:
                log.error("Worker exception: %s", exc)
                errors += 1
            else:
                if result["status"] == "tagged":
                    tagged += 1
                    if result.get("needs_review"):
                        needs_review_count += 1
                elif result["status"] == "error":
                    errors += 1
                else:
                    skipped += 1
            if self._stop_event.is_set():
                for f in futures:
                    f.cancel()
                break
        return {"tagged": tagged, "skipped": skipped, "errors": errors,
                "needs_review": needs_review_count}

    def _needs_analysis_fast(self, fp: str, tracked: dict) -> bool:
        """Check if a file needs analysis against bulk-loaded DB data."""
        rec = tracked.get(fp)
        if rec is None:
            return True
        file_hash, status, locked = rec
        if locked:
            return False
        if status != "done":
            return True
        return file_hash != get_file_hash(fp)

    def scan_directory(self, force: bool = False) -> dict:
        self._stop_event.clear()
        self._pause_event.set()
        self.progress.set_paused(False)

        # Phase 1 — discovery: register every audio file as 'pending' so the
        # full library is visible in the UI immediately, before analysis begins.
        entries: list[tuple[str, str]] = []
        for root, _, files in os.walk(self.config["music_dir"]):
            for fname in sorted(files):
                if Path(fname).suffix.lower() in self.config["extensions"]:
                    fp = os.path.join(root, fname)
                    entries.append((fp, get_file_hash(fp)))

        if not force:
            # Auto-detect stale pre-tag hashes: if the majority of already-done
            # tracks show hash mismatches it almost certainly means the DB was
            # built by an older version that stored the hash before writing the
            # BPM tag.  Refresh stored hashes in-place so the upcoming
            # bulk_register_pending doesn't re-queue the whole library.
            existing = self.db.get_all_file_hashes()
            entry_map = dict(entries)
            done_tracked = [(fp, h) for fp, (h, s, _l) in existing.items()
                            if s == "done" and not _l and fp in entry_map]
            if done_tracked:
                mismatches = sum(1 for fp, h in done_tracked if h != entry_map[fp])
                ratio = mismatches / len(done_tracked)
                if mismatches > 10 and ratio > 0.5:
                    log.warning(
                        "Hash mismatch on %.0f%% of done tracks (%d/%d) — "
                        "auto-refreshing stored hashes (stale pre-tag hashes detected; "
                        "this happens once after upgrading from an older version)",
                        ratio * 100, mismatches, len(done_tracked),
                    )
                    self.db.refresh_hashes()
                    # Recompute entries so bulk_register uses the fresh hashes
                    entries = [(fp, get_file_hash(fp)) for fp, _ in entries]

        self.db.bulk_register_pending(entries, force=force)
        log.info("Scan phase 1 complete — %d audio files registered", len(entries))

        # Detect files that were previously tracked but are no longer on disk.
        # Locked tracks are intentionally excluded — they may live on an external
        # drive that is temporarily unmounted and we don't want to lose the lock.
        discovered_paths = {fp for fp, _ in entries}
        all_tracked = self.db.get_all_file_hashes()
        deleted_paths = {
            fp for fp, (_hash, status, locked) in all_tracked.items()
            if fp not in discovered_paths and not locked and status != "deleted"
        }
        if deleted_paths:
            log.info("Detected %d deleted file(s) — marking as deleted in DB", len(deleted_paths))
            self.db.mark_deleted_bulk(deleted_paths)

        # Phase 2 — processing: work through every pending track.
        # bulk_register_pending already filtered out locked + (when !force)
        # unchanged done tracks, so everything in the queue needs analysis.
        queue = self.db.get_pending_tracks()
        log.info("Scan phase 2 — %d tracks queued for analysis", len(queue))

        self.progress.start(len(queue))
        counts = self._process_files_parallel(queue, force=True)
        self.progress.finish()
        self._finish_scan(counts, "Scan")
        self.index_tags()
        return counts

    def index_tags(self) -> int:
        """Read mutagen metadata into the DB for rows whose tags are unread or
        whose file changed (grabber library-match index). Gated by index_tags."""
        if not self.config.get("index_tags", True):
            return 0
        from ..bpm.tags import read_tags
        from ..grabber.matching import normalize_artist, normalize_title

        rows = self.db.get_tracks_needing_tag_index()
        updated = 0
        for row in rows:
            fp = row["file_path"]
            if not os.path.exists(fp):
                continue
            tags = read_tags(fp)
            tags["norm_title"] = normalize_title(tags.get("title"))
            tags["norm_artist"] = normalize_artist(tags.get("artist"))
            self.db.update_track_tags(fp, tags, get_file_hash(fp))
            updated += 1
        if updated:
            log.info("Tag index: updated metadata for %d track(s)", updated)
        return updated

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

    def retry_errors(self) -> dict:
        """Re-analyze only tracks with status='error'."""
        self._stop_event.clear()
        self._pause_event.set()
        self.progress.set_paused(False)

        queue = self.db.get_error_tracks()
        log.info("retry_errors: %d error tracks queued", len(queue))

        self.progress.start(len(queue))
        counts = self._process_files_parallel(queue, force=True)
        self.progress.finish()
        self._finish_scan(counts, "retry_errors")
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

        handler = WatchHandler(self)
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
