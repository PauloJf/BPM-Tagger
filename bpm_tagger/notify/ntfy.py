"""Notification manager — anti-spam batching of ntfy pushes."""

import logging
import threading
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)


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

    def send_grabber(self, title: str, body: str, click_url: str = "",
                     priority: str = "default", tags: str = "arrow_down", actions: str = ""):
        """Grabber-specific push (bypasses batching). Ambiguity/failure use high
        priority + a click URL/action into the inbox; ntfy being down never raises."""
        self._post(title, body, tags, priority=priority, click_url=click_url, actions=actions)

    def _post(self, title: str, body: str, tag: str, priority: str = "low",
              click_url: str = "", actions: str = ""):
        try:
            headers = {"Title": title, "Priority": priority, "Tags": tag}
            if click_url:
                headers["Click"] = click_url
            if actions:
                headers["Actions"] = actions
            resp = requests.post(
                f"{self._url}/{self._topic}",
                data=body.encode(),
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            log.debug("ntfy notification sent: %s", title)
        except Exception as exc:
            log.warning("ntfy notification failed: %s", exc)
