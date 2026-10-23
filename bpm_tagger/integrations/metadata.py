"""Track-metadata candidate gathering for the "Find metadata" editor action.

Queries Deezer (keyless) for complete tag sets — title, artist, album, album artist, track/disc number,
year, ISRC, duration — either directly **by ISRC** (Deezer supports it)
or by an artist/title text search. Every source is best-effort: failures are
logged and skipped, mirroring integrations.isrc.
"""

import logging

import requests

from .ratelimit import deezer_limiter

log = logging.getLogger(__name__)

_SEARCH_LIMIT = 4  # Deezer search hits get a per-track details fetch each


def _deezer_normalize(d: dict) -> dict | None:
    """Full Deezer track object → our candidate shape. None for error payloads."""
    if not d or d.get("error") or not d.get("id"):
        return None
    album = d.get("album") or {}
    rel = str(d.get("release_date") or "")
    return {
        "source": "deezer",
        "title": d.get("title_short") or d.get("title") or "",
        "artist": (d.get("artist") or {}).get("name", ""),
        "album": album.get("title", ""),
        # Deezer's track payload carries no album artist; the track artist is
        # the right default for anything except compilations (user-editable).
        "album_artist": (d.get("artist") or {}).get("name", ""),
        "track_no": d.get("track_position"),
        "disc_no": d.get("disk_number"),
        "year": int(rel[:4]) if rel[:4].isdigit() else None,
        "isrc": (d.get("isrc") or "").upper(),
        "duration_ms": (d.get("duration") or 0) * 1000 or None,
        "cover_url": album.get("cover_xl") or album.get("cover_big") or "",
        "url": d.get("link", ""),
    }


def _deezer_get(path: str) -> dict:
    deezer_limiter.acquire()
    resp = requests.get(f"https://api.deezer.com/{path.lstrip('/')}", timeout=8)
    resp.raise_for_status()
    return resp.json()


def _deezer_by_isrc(isrc: str) -> list[dict]:
    cand = _deezer_normalize(_deezer_get(f"track/isrc:{isrc}"))
    return [cand] if cand else []


def deezer_search(artist: str, title: str, q: str) -> list[dict]:
    query = q or (f'artist:"{artist}" track:"{title}"' if artist and title
                  else f"{artist} {title}".strip())
    deezer_limiter.acquire()
    resp = requests.get("https://api.deezer.com/search",
                        params={"q": query, "limit": _SEARCH_LIMIT}, timeout=8)
    resp.raise_for_status()
    hits = resp.json().get("data") or []
    out = []
    for h in hits[:_SEARCH_LIMIT]:
        # Search results are shallow (no track/disc/year/ISRC) — fetch details.
        try:
            cand = _deezer_normalize(_deezer_get(f"track/{h.get('id')}"))
            if cand:
                out.append(cand)
        except Exception as exc:
            log.debug("Deezer track details failed for %s: %s", h.get("id"), exc)
    return out


def gather_metadata(artist: str = "", title: str = "",
                    isrc: str = "", q: str = "") -> list[dict]:
    """Metadata candidates from Deezer. With an ISRC it is asked for that exact
    recording; otherwise an artist/title (or free ``q``) search runs."""
    out: list[dict] = []
    try:
        if isrc:
            out.extend(_deezer_by_isrc(isrc))
        elif artist or title or q:
            out.extend(deezer_search(artist, title, q))
    except Exception as exc:
        log.warning("Deezer metadata lookup failed: %s", exc)
    return out
