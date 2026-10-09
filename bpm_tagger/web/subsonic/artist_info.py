"""Artist info for Subsonic ``getArtistInfo2``: biography, similar artists and a
photo, from the same sources and the same 24 h cache as the web UI's Related
panel and artist popup (so an artist looked up in one is instant in the other).

* biography — MusicBrainz → Wikidata → Wikipedia summary (``artist_bio``);
* similar artists — Deezer's related artists, kept only when they're in the
  library (and visible to the caller), so every one links to a real artist page;
* photo URLs — the artist's Deezer picture. Apps load these straight from
  Deezer's CDN, so they're only offered when ``fetch_artist_images`` is on (the
  same opt-in the web UI's online artist images use).

Gated by ``SUBSONIC_ARTIST_INFO`` (on). The lookups can take seconds
(MusicBrainz allows one request a second), so they run in the background: the
request waits up to ``WAIT_S`` and answers with whatever is cached by then; a
slower lookup finishes and fills the cache for the next visit.
"""

import logging
import threading

from ...text import normalize_artist

log = logging.getLogger(__name__)

WAIT_S = 3.0
MAX_LOOKUPS = 2

_slots = threading.BoundedSemaphore(MAX_LOOKUPS)
_lock = threading.Lock()
_inflight: dict[str, threading.Event] = {}


def _cache():
    from ..api.suggestions import _cache_get, _cache_put
    return _cache_get, _cache_put


def _keys(name: str) -> dict:
    n = normalize_artist(name)
    return {"bio": "bio:" + n, "related": "artists:" + n, "self": "dzartist:" + n}


def cached(name: str) -> dict:
    """Whatever is already cached: {bio: str|None, related: list|None, self: dict|None}."""
    get, _ = _cache()
    k = _keys(name)
    bio = get(k["bio"])
    return {"bio": None if bio is None else bio.get("description", ""),
            "related": get(k["related"]), "self": get(k["self"])}


def _run(name: str, done: threading.Event) -> None:
    get, put = _cache()
    k = _keys(name)
    try:
        from ...integrations import deezer_catalog as dz
        hit = get(k["self"])
        if hit is None:
            hit = dz.search_artist(name) or {}
            put(k["self"], hit)
        if get(k["related"]) is None:
            rels = dz.related_artists(hit["dz_id"]) if hit.get("dz_id") else []
            put(k["related"], [{"dz_id": r["dz_id"], "name": r["name"],
                                "image_url": r["image_url"]} for r in rels])
        if get(k["bio"]) is None:
            from ...integrations.artist_info import artist_bio
            put(k["bio"], {"description": artist_bio(name)})
    except Exception as exc:  # network trouble: an empty answer this time
        log.debug("Subsonic artist info lookup failed for %s: %s", name, exc)
    finally:
        _slots.release()
        with _lock:
            _inflight.pop(k["bio"], None)
        done.set()


def lookup(name: str) -> dict:
    """Cached info, fetching what's missing with a short wait (see module doc)."""
    have = cached(name)
    if all(v is not None for v in have.values()):
        return have
    key = _keys(name)["bio"]
    with _lock:
        done = _inflight.get(key)
        if done is None:
            if not _slots.acquire(blocking=False):
                return have
            done = threading.Event()
            _inflight[key] = done
            threading.Thread(target=_run, args=(name, done), name="subsonic-artist-info",
                             daemon=True).start()
    done.wait(WAIT_S)
    return cached(name)
