"""Keyless Deezer public-catalog client for suggestions & related lookups.

Deezer's public API needs no account, key or OAuth. This module wraps the
handful of endpoints the Suggestions page and the Related panel use:

    search/artist        name → Deezer artist id (+ picture)
    artist/{id}/related  ~20 similar artists (with images)
    artist/{id}/top      an artist's top tracks (30-s preview URLs)
    artist/{id}/radio    ~25 tracks "in the style of" an artist
    track/{id}           full track object incl. ISRC (fetched lazily)

Modeled on integrations/metadata.py: module-level functions, ``requests`` with
short timeouts, ``deezer_limiter.acquire()`` before every call, and
log-and-return-empty on any failure (a dead seed must never abort a refresh).

NOTE: Deezer reports track duration in SECONDS; every track shape here converts
it to milliseconds to match the rest of the codebase.
"""

import logging
from typing import Optional

import requests

from ..grabber.matching import normalize_artist
from .ratelimit import deezer_limiter

log = logging.getLogger(__name__)

_TIMEOUT = 8


def _get(path: str, params: Optional[dict] = None) -> dict:
    """One rate-limited GET against api.deezer.com. Raises on HTTP error."""
    deezer_limiter.acquire()
    resp = requests.get(f"https://api.deezer.com/{path.lstrip('/')}",
                        params=params, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json() or {}


def _artist_shape(a: dict) -> Optional[dict]:
    """Deezer artist object → {dz_id, name, image_url}. None for junk/errors."""
    if not a or a.get("error") or not a.get("id"):
        return None
    return {
        "dz_id": str(a.get("id")),
        "name": a.get("name") or "",
        "image_url": a.get("picture_xl") or a.get("picture_big")
        or a.get("picture_medium") or a.get("picture") or "",
    }


def _track_shape(t: dict) -> Optional[dict]:
    """Deezer track object → our track meta. None for junk/errors.

    Deezer duration is in seconds → ms. ``artist``/``album`` come as nested
    objects on catalog endpoints (top/radio)."""
    if not t or t.get("error") or not t.get("id"):
        return None
    album = t.get("album") or {}
    artist = t.get("artist") or {}
    return {
        "dz_track_id": str(t.get("id")),
        "title": t.get("title_short") or t.get("title") or "",
        "artist": artist.get("name") or "",
        "album": album.get("title") or "",
        "duration_ms": (t.get("duration") or 0) * 1000 or None,
        "cover_url": album.get("cover_xl") or album.get("cover_big")
        or album.get("cover_medium") or album.get("cover") or "",
        "preview_url": t.get("preview") or "",
    }


def search_artist(name: str) -> Optional[dict]:
    """Resolve an artist name to the best Deezer hit ({dz_id, name, image_url}).

    Prefers a hit whose normalized name equals the query; otherwise the first
    result (Deezer orders by relevance/fans). None when nothing resolves."""
    if not name or not name.strip():
        return None
    try:
        hits = (_get("search/artist", {"q": name, "limit": 10}).get("data")) or []
    except Exception as exc:
        log.debug("Deezer artist search failed for %r: %s", name, exc)
        return None
    shaped = [s for s in (_artist_shape(a) for a in hits) if s]
    if not shaped:
        return None
    want = normalize_artist(name)
    for s in shaped:
        if normalize_artist(s["name"]) == want:
            return s
    return shaped[0]


def related_artists(dz_id: str) -> list[dict]:
    """~20 artists related to a Deezer artist id. Empty list on failure."""
    if not dz_id:
        return []
    try:
        data = (_get(f"artist/{dz_id}/related", {"limit": 20}).get("data")) or []
    except Exception as exc:
        log.debug("Deezer related artists failed for %s: %s", dz_id, exc)
        return []
    return [s for s in (_artist_shape(a) for a in data) if s]


def artist_top_tracks(dz_id: str, limit: int = 5) -> list[dict]:
    """An artist's top tracks. Empty list on failure."""
    if not dz_id:
        return []
    try:
        data = (_get(f"artist/{dz_id}/top", {"limit": limit}).get("data")) or []
    except Exception as exc:
        log.debug("Deezer top tracks failed for %s: %s", dz_id, exc)
        return []
    return [s for s in (_track_shape(t) for t in data) if s]


def artist_radio(dz_id: str, limit: int = 25) -> list[dict]:
    """~25 tracks "in the style of" an artist — the similar-tracks source.
    Empty list on failure."""
    if not dz_id:
        return []
    try:
        data = (_get(f"artist/{dz_id}/radio", {"limit": limit}).get("data")) or []
    except Exception as exc:
        log.debug("Deezer artist radio failed for %s: %s", dz_id, exc)
        return []
    return [s for s in (_track_shape(t) for t in data) if s]


def get_artist(dz_id: str) -> Optional[dict]:
    """Full artist object: {dz_id, name, image_url, nb_fan, nb_album}. None on failure."""
    if not dz_id:
        return None
    try:
        d = _get(f"artist/{dz_id}")
    except Exception as exc:
        log.debug("Deezer artist fetch failed for %s: %s", dz_id, exc)
        return None
    s = _artist_shape(d)
    if not s:
        return None
    s["nb_fan"] = d.get("nb_fan") or 0
    s["nb_album"] = d.get("nb_album") or 0
    return s


def _album_shape(a: dict) -> Optional[dict]:
    """Deezer album (list or full) → our album meta. None for junk/errors."""
    if not a or a.get("error") or not a.get("id"):
        return None
    rel = str(a.get("release_date") or "")
    return {
        "dz_album_id": str(a.get("id")),
        "title": a.get("title") or "",
        "cover_url": a.get("cover_xl") or a.get("cover_big")
        or a.get("cover_medium") or a.get("cover") or "",
        # album | single | ep | compilation
        "record_type": (a.get("record_type") or "album").lower(),
        "year": int(rel[:4]) if rel[:4].isdigit() else None,
        "release_date": rel,
        "nb_tracks": a.get("nb_tracks") or 0,
        "explicit": bool(a.get("explicit_lyrics")),
    }


def artist_albums(dz_id: str, limit: int = 100) -> list[dict]:
    """An artist's discography (albums + singles + EPs), newest first. Empty on failure."""
    if not dz_id:
        return []
    try:
        data = (_get(f"artist/{dz_id}/albums", {"limit": limit}).get("data")) or []
    except Exception as exc:
        log.debug("Deezer artist albums failed for %s: %s", dz_id, exc)
        return []
    return [s for s in (_album_shape(a) for a in data) if s]


def album(album_id: str) -> Optional[dict]:
    """A full album with its tracklist. Each track carries the album's title +
    cover (album endpoints don't nest those per track). None on failure."""
    if not album_id:
        return None
    try:
        d = _get(f"album/{album_id}")
    except Exception as exc:
        log.debug("Deezer album fetch failed for %s: %s", album_id, exc)
        return None
    shape = _album_shape(d)
    if not shape:
        return None
    album_artist = (d.get("artist") or {}).get("name") or ""
    tracks = []
    for t in ((d.get("tracks") or {}).get("data")) or []:
        if not t or not t.get("id"):
            continue
        tracks.append({
            "dz_track_id": str(t.get("id")),
            "title": t.get("title_short") or t.get("title") or "",
            "artist": (t.get("artist") or {}).get("name") or album_artist,
            "album": shape["title"],
            "duration_ms": (t.get("duration") or 0) * 1000 or None,
            "cover_url": shape["cover_url"],
            "preview_url": t.get("preview") or "",
        })
    shape["artist"] = album_artist
    shape["tracks"] = tracks
    return shape


def track_isrc(dz_track_id: str) -> str:
    """The ISRC for a Deezer track id (upper-cased), "" on failure.

    Fetched lazily on enqueue only — the catalog list endpoints don't carry it."""
    if not dz_track_id:
        return ""
    try:
        d = _get(f"track/{dz_track_id}")
    except Exception as exc:
        log.debug("Deezer track ISRC lookup failed for %s: %s", dz_track_id, exc)
        return ""
    return (d.get("isrc") or "").upper()


def track_preview_url(dz_track_id: str) -> str:
    """The 30-second preview MP3 URL for a Deezer track id, "" on failure
    or when Deezer has no preview for the track.

    Same ``track/{id}`` endpoint as ``track_isrc()`` — kept separate because the
    call sites differ and both are cheap + cached upstream."""
    if not dz_track_id:
        return ""
    try:
        d = _get(f"track/{dz_track_id}")
    except Exception as exc:
        log.debug("Deezer preview lookup failed for %s: %s", dz_track_id, exc)
        return ""
    return d.get("preview") or ""


def track_by_isrc(isrc: str) -> dict:
    """Resolve an ISRC to {"dz_track_id": str, "preview_url": str}; {} on failure.

    Used to preview the *source* Spotify track an inbox item is trying to fulfil —
    most Spotify-sourced items carry an ISRC."""
    if not isrc:
        return {}
    try:
        d = _get(f"track/isrc:{isrc.strip().upper()}")
    except Exception as exc:
        log.debug("Deezer ISRC lookup failed for %s: %s", isrc, exc)
        return {}
    if not d.get("id"):
        return {}
    return {"dz_track_id": str(d["id"]), "preview_url": d.get("preview") or ""}
