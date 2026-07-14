"""Navidrome / Subsonic integration.

Two concerns live here:

* Library rescan trigger (`startScan`) and a connection test (`ping`).
* The Subsonic client used by the two-way star sync (`get_starred`, `resolve_id`,
  `set_star`) — see docs/plans/navidrome-star-sync.md.

Subsonic keys songs by an opaque `id`; BPM Tagger keys by `file_path`. Identity is
resolved fast-path by path suffix and falls back to the grabber's fuzzy matcher.
"""

import hashlib
import logging
import secrets

import requests

from ..grabber.matching import score

log = logging.getLogger(__name__)


def _sub_params(user: str, pwd: str) -> dict:
    """Salted-token Subsonic auth params (shared by every /rest call)."""
    salt = secrets.token_hex(6)
    token = hashlib.md5((pwd + salt).encode()).hexdigest()
    return {"u": user, "t": token, "s": salt, "v": "1.8.0", "c": "bpm-tagger", "f": "json"}


def _sub_response(resp: requests.Response) -> dict:
    """Raise on HTTP error, then return the inner subsonic-response object."""
    resp.raise_for_status()
    return (resp.json() or {}).get("subsonic-response", {}) or {}


def ping_navidrome(url: str, user: str, pwd: str) -> tuple[bool, str]:
    """Subsonic /rest/ping connection test. Returns (ok, message)."""
    if not (url and user and pwd):
        return False, "URL, username and password are required"
    try:
        resp = requests.get(
            f"{url.rstrip('/')}/rest/ping",
            params=_sub_params(user, pwd),
            timeout=10,
        )
        sub = _sub_response(resp)
        if sub.get("status") == "ok":
            return True, "Connected"
        err = (sub.get("error") or {}).get("message", "authentication failed")
        return False, err
    except Exception as exc:
        return False, str(exc)


def _trigger_navidrome_rescan(config: dict):
    url  = config.get("navidrome_url", "").rstrip("/")
    user = config.get("navidrome_user", "")
    pwd  = config.get("navidrome_pass", "")
    if not (url and user and pwd):
        return
    try:
        resp = requests.get(
            f"{url}/rest/startScan",
            params=_sub_params(user, pwd),
            timeout=10,
        )
        _sub_response(resp)
        log.info("Navidrome rescan triggered")
    except Exception as exc:
        log.warning("Navidrome rescan request failed: %s", exc)


# ── Star sync (Subsonic client) ───────────────────────────────────────────────

def _norm_path(p: str) -> str:
    return (p or "").replace("\\", "/").lower().rstrip("/")


def _paths_match(local_path: str, remote_path: str) -> bool:
    """True if two paths refer to the same file despite differing roots.

    Navidrome's container root (e.g. /music/...) rarely equals BPM Tagger's
    MUSIC_DIR, so match on a segment-aligned suffix rather than requiring equality.
    Requiring the shorter to align on a '/' boundary in the longer avoids matching
    on a bare filename collision.
    """
    a, b = _norm_path(local_path), _norm_path(remote_path)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if not long.endswith(short):
        return False
    if "/" not in short.lstrip("/"):
        return False  # need at least 'dir/file', never match on a bare filename
    idx = len(long) - len(short)
    # The suffix must start on a path-segment boundary in `long`.
    return idx == 0 or short.startswith("/") or long[idx - 1] == "/"


def _to_match_dict(song: dict) -> dict:
    """Adapt a Subsonic song object to the keys matching.score() expects."""
    return {
        "title": song.get("title"),
        "artist": song.get("artist"),
        "album": song.get("album"),
        "duration_ms": (song.get("duration") or 0) * 1000,  # Subsonic duration is seconds
        "isrc": None,  # Subsonic doesn't expose ISRC on the song object
    }


def best_match_id(track: dict, hits: list[dict], threshold: float = 0.80) -> str | None:
    """Pick the Subsonic song id for a local track from candidate `hits`.

    Fast path: a segment-aligned path-suffix match. Fallback: the best fuzzy
    matching.score() over the hits, accepted only at/above `threshold`.
    `track` uses the keys from db.all_tracks_for_star_sync() (incl. file_path).
    """
    local_path = track.get("file_path") or ""
    if local_path:
        for h in hits:
            if h.get("path") and _paths_match(local_path, h["path"]):
                return h.get("id")

    best_id, best = None, 0.0
    for h in hits:
        s, _ = score(track, _to_match_dict(h))
        if s > best:
            best, best_id = s, h.get("id")
    return best_id if best >= threshold else None


def get_starred(url: str, user: str, pwd: str) -> list[dict]:
    """Every currently-starred song on the server.

    ONE call returns the whole remote starred set (each song carries `path`), so a
    pull needs no library paging. Returns the raw Subsonic song objects.
    """
    resp = requests.get(
        f"{url.rstrip('/')}/rest/getStarred2",
        params=_sub_params(user, pwd),
        timeout=30,
    )
    sub = _sub_response(resp)
    return (sub.get("starred2", {}) or {}).get("song", []) or []


def resolve_id(url: str, user: str, pwd: str, track: dict, threshold: float = 0.80) -> str | None:
    """Resolve a local track to a Subsonic song id via search3 (for pushing a star
    OUT when the track isn't in the starred set). Returns None if nothing matches
    confidently enough."""
    q = f'{track.get("artist", "")} {track.get("title", "")}'.strip()
    if not q:
        return None
    try:
        resp = requests.get(
            f"{url.rstrip('/')}/rest/search3",
            params={**_sub_params(user, pwd), "query": q, "songCount": 20,
                    "artistCount": 0, "albumCount": 0},
            timeout=20,
        )
        hits = (_sub_response(resp).get("searchResult3", {}) or {}).get("song", []) or []
    except Exception as exc:
        log.warning("Navidrome search3 failed for %r: %s", q, exc)
        return None
    return best_match_id(track, hits, threshold)


def set_star(url: str, user: str, pwd: str, song_id: str, starred: bool) -> bool:
    """Star or unstar a song by id. Returns True only on a Subsonic 'ok' status;
    the caller advances the sync baseline only when this returns True."""
    ep = "star" if starred else "unstar"
    try:
        resp = requests.get(
            f"{url.rstrip('/')}/rest/{ep}",
            params={**_sub_params(user, pwd), "id": song_id},
            timeout=15,
        )
        return _sub_response(resp).get("status") == "ok"
    except Exception as exc:
        log.warning("Navidrome %s failed for id=%s: %s", ep, song_id, exc)
        return False
