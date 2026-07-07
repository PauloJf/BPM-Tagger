"""Shared ISRC candidate gathering for the "Find ISRC" and bulk-fill features.

Queries Deezer (your ARL), Spotify (if connected) and MusicBrainz for the ISRCs
of an artist+title, carrying each source's duration so a fill can require a
duration match before trusting a result (guards against grabbing a remix/live
version's ISRC). Every source is best-effort — failures are logged and skipped.
"""

import logging

from ..grabber.providers.base import TrackMeta
from .musicbrainz import lookup_isrcs

log = logging.getLogger(__name__)


def gather_candidates(config: dict, spotify_client, artist: str, title: str) -> list[dict]:
    """Return ISRC candidates [{source, isrc, title, artist, duration_ms, url}]
    from Deezer + Spotify + MusicBrainz, deduped by ISRC."""
    query = f"{artist} {title}".strip()
    out: list[dict] = []
    seen: set[str] = set()

    def add(source, isrc, t, a, dur, url):
        code = (isrc or "").upper()
        if code and code not in seen:
            seen.add(code)
            out.append({"source": source, "isrc": code, "title": t or "",
                        "artist": a or "", "duration_ms": dur, "url": url})

    if config.get("deezer_arl"):
        try:
            from ..grabber.providers.deezer import DeezerProvider
            for c in DeezerProvider(config).search(TrackMeta(title=title, artist=artist), limit=5):
                url = f"https://www.deezer.com/track/{c.provider_track_id}" if c.provider_track_id else ""
                add("deezer", c.isrc, c.title, c.artist, c.duration_ms, url)
        except Exception as exc:
            log.warning("Deezer ISRC lookup failed: %s", exc)

    if spotify_client is not None:
        try:
            if spotify_client.is_connected():
                for t in spotify_client.search_tracks(query, limit=5):
                    sid = t.get("spotify_track_id")
                    add("spotify", t.get("isrc"), t.get("title"), t.get("artist"),
                        t.get("duration_ms"),
                        f"https://open.spotify.com/track/{sid}" if sid else "")
        except Exception as exc:
            log.warning("Spotify ISRC lookup failed: %s", exc)

    try:
        for c in lookup_isrcs(artist, title):
            add("musicbrainz", c.get("isrc"), c.get("title"), c.get("artist"),
                c.get("duration_ms"), c.get("url", ""))
    except Exception as exc:
        log.warning("MusicBrainz ISRC lookup failed: %s", exc)

    return out


def pick_confident(candidates: list[dict], track_duration_ms, tol_ms: int = 3000):
    """Return a single ISRC only when it's a confident match: a unique ISRC whose
    duration is within tolerance of the track. If we have no track duration, only
    accept a single unambiguous ISRC across all sources. Otherwise return None
    (ambiguous → leave it for the user to choose)."""
    if not candidates:
        return None
    if not track_duration_ms:
        isrcs = {c["isrc"] for c in candidates}
        return next(iter(isrcs)) if len(isrcs) == 1 else None
    matched = {c["isrc"] for c in candidates
               if c.get("duration_ms") and abs(c["duration_ms"] - track_duration_ms) <= tol_ms}
    return next(iter(matched)) if len(matched) == 1 else None
