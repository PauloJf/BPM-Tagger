"""A tiny registry of short background work that has no progress object of its own
(the Navidrome syncs, manual or scheduled), so the UI can show that something is
running. Long jobs with their own counters (loudness / waveform / lyrics / ISRC
fills, the scan itself) are read where they live; see ``web/api/activity.py``.

Stdlib only and thread-safe. The same key can be entered by two threads at once (a
manual sync during a scheduled one): the item stays listed until the last one ends.
"""

import threading
from contextlib import contextmanager

_lock = threading.Lock()
_active: dict[str, dict] = {}


@contextmanager
def track(key: str, label: str):
    """``with track("sync", "Syncing Navidrome"): ...`` lists the work while it runs."""
    with _lock:
        item = _active.setdefault(key, {"label": label, "count": 0})
        item["count"] += 1
    try:
        yield
    finally:
        with _lock:
            item = _active.get(key)
            if item is not None:
                item["count"] -= 1
                if item["count"] <= 0:
                    del _active[key]


def snapshot() -> list[dict]:
    """[{id, label}] for everything currently running, oldest first."""
    with _lock:
        return [{"id": k, "label": v["label"]} for k, v in _active.items()]
