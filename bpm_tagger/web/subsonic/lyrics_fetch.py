"""On-demand lyrics for Subsonic apps (opt-in: ``SUBSONIC_FETCH_LYRICS``).

When an app asks for a song's lyrics and the file has none, look them up on
LRCLIB and store them exactly as the web UI's fetch does (``lyrics_mode``:
embedded tag or ``.lrc`` sidecar; ``PRESERVE_MTIME`` honoured; the track's
lyrics state recorded). Every app — and the web player — then has them.

LRCLIB can take many seconds (its client allows up to 15 + 25 s), and a server
thread shouldn't be held that long, so the lookup runs in the background: the
request waits up to ``WAIT_S`` and returns whatever is there by then. A slow
lookup still completes and saves, so the next request gets the lyrics. At most
``MAX_LOOKUPS`` run at once, and concurrent requests for the same song share one
lookup. Songs LRCLIB said it has nothing for (``not_found``) or are
instrumental are never looked up again — retry those from the web UI.
"""

import logging
import threading

log = logging.getLogger(__name__)

WAIT_S = 4.0
MAX_LOOKUPS = 2
_SKIP_STATES = ("not_found", "instrumental")

_slots = threading.BoundedSemaphore(MAX_LOOKUPS)
_lock = threading.Lock()
_inflight: dict[str, threading.Event] = {}


def eligible(st, track: dict) -> bool:
    return (bool(st.config.get("subsonic_fetch_lyrics"))
            and bool(track.get("artist")) and bool(track.get("title"))
            and track.get("lyrics_status") not in _SKIP_STATES)


def _run(st, track: dict, done: threading.Event) -> None:
    path = track["file_path"]
    try:
        from ...integrations.lrclib import fetch_lyrics
        from ..api.lyrics import _apply_fetched
        result = fetch_lyrics(track["artist"], track["title"], track.get("album") or "",
                              track.get("duration_ms"))
        if result:
            _apply_fetched(st, path, result)
            log.info("Subsonic: fetched lyrics for %s – %s", track["artist"], track["title"])
        else:
            st.db.set_lyrics_state(path, "not_found", False)
    except Exception as exc:  # network / write failure: just no lyrics this time
        log.warning("Subsonic lyrics fetch failed for %s: %s", path, exc)
    finally:
        _slots.release()
        with _lock:
            _inflight.pop(path, None)
        done.set()


def fetch_and_wait(st, track: dict) -> bool:
    """Start (or join) a lookup for this track and wait up to WAIT_S.
    True once it finished within the wait; False if it's still running, all
    lookup slots were busy, or the track isn't eligible."""
    if not eligible(st, track):
        return False
    path = track["file_path"]
    with _lock:
        done = _inflight.get(path)
        if done is None:
            if not _slots.acquire(blocking=False):
                return False
            done = threading.Event()
            _inflight[path] = done
            threading.Thread(target=_run, args=(st, track, done),
                             name="subsonic-lyrics", daemon=True).start()
    return done.wait(WAIT_S)
