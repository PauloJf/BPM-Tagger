"""LRCLIB (lrclib.net) lyrics lookup — free, keyless, plain + synced (LRC).

Two-step resolution: the exact-signature ``/api/get`` (artist + title + album +
duration) when we know enough about the track, falling back to ``/api/search``
with a duration-tolerance filter so a remix/live version's lyrics aren't
grabbed for the studio cut. Every failure returns None — lyrics are always
best-effort and must never fail a caller's pipeline.
"""

import logging

import requests

from ..config import __version__

log = logging.getLogger(__name__)

API_BASE = "https://lrclib.net/api"
# LRCLIB asks for a descriptive User-Agent from well-behaved clients.
_HEADERS = {"User-Agent": f"BPM-Tagger/{__version__} (https://github.com/PauloJf/BPM-Tagger)"}
_DURATION_TOL_S = 4


def _record_to_result(rec: dict) -> dict | None:
    """Normalize an LRCLIB record to {'plain','synced','instrumental'} or None."""
    if not rec:
        return None
    if rec.get("instrumental"):
        return {"plain": "", "synced": "", "instrumental": True}
    plain = (rec.get("plainLyrics") or "").strip()
    synced = (rec.get("syncedLyrics") or "").strip()
    if not plain and not synced:
        return None
    return {"plain": plain, "synced": synced, "instrumental": False}


def _get_exact(artist: str, title: str, album: str, duration_s: int) -> dict | None:
    resp = requests.get(f"{API_BASE}/get", params={
        "artist_name": artist, "track_name": title,
        "album_name": album, "duration": duration_s,
    }, headers=_HEADERS, timeout=15)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return _record_to_result(resp.json())


def _search(artist: str, title: str, duration_s: int | None) -> dict | None:
    # The search endpoint is markedly slower than /get under load; give it
    # more room — a miss degrades gracefully to "not found" either way.
    resp = requests.get(f"{API_BASE}/search", params={
        "artist_name": artist, "track_name": title,
    }, headers=_HEADERS, timeout=25)
    resp.raise_for_status()
    records = resp.json() or []
    if duration_s:
        records = [r for r in records
                   if r.get("duration") and abs(r["duration"] - duration_s) <= _DURATION_TOL_S]
    # Prefer a synced result over a plain-only one, then LRCLIB's own ranking.
    records.sort(key=lambda r: 0 if (r.get("syncedLyrics") or "").strip() else 1)
    for rec in records:
        result = _record_to_result(rec)
        if result:
            return result
    return None


def fetch_lyrics(artist: str, title: str, album: str = "",
                 duration_ms: int | None = None) -> dict | None:
    """Look up lyrics for a track.

    Returns {'plain': str, 'synced': str, 'instrumental': bool} or None when
    nothing was found (or the lookup failed).
    """
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not artist or not title:
        return None
    duration_s = round(duration_ms / 1000) if duration_ms else None
    try:
        if album and duration_s:
            result = _get_exact(artist, title, album.strip(), duration_s)
            if result:
                return result
        return _search(artist, title, duration_s)
    except Exception as exc:
        log.warning("LRCLIB lookup failed for %s – %s: %s", artist, title, exc)
        return None
