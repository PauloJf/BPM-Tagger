"""Shared ISRC candidate gathering for the "Find ISRC" and bulk-fill features.

Queries Deezer (keyless) and MusicBrainz for the ISRCs of an artist+title, carrying each source's duration so a fill can require a
duration match before trusting a result (guards against grabbing a remix/live
version's ISRC). Every source is best-effort — failures are logged and skipped.
"""

import logging

from .metadata import deezer_search
from .musicbrainz import lookup_isrcs

log = logging.getLogger(__name__)


def gather_candidates(artist: str, title: str) -> list[dict]:
    """Return ISRC candidates [{source, isrc, title, artist, duration_ms, url}]
    from Deezer + MusicBrainz, deduped by ISRC."""
    out: list[dict] = []
    seen: set[str] = set()

    def add(source, isrc, t, a, dur, url):
        code = (isrc or "").upper()
        if code and code not in seen:
            seen.add(code)
            out.append({"source": source, "isrc": code, "title": t or "",
                        "artist": a or "", "duration_ms": dur, "url": url})

    try:
        for c in deezer_search(artist, title, ""):
            add("deezer", c.get("isrc"), c.get("title"), c.get("artist"),
                c.get("duration_ms"), c.get("url", ""))
    except Exception as exc:
        log.warning("Deezer ISRC lookup failed: %s", exc)

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
