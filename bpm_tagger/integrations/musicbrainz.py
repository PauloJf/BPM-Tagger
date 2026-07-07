"""MusicBrainz ISRC lookup — the no-account fallback for the "Find ISRC" feature.

Searches recordings by artist+title, then fetches ISRCs for the best matches.
Free and keyless, but rate-limited (~1 req/s) and requires a descriptive
User-Agent per MusicBrainz policy. Best-effort: any failure returns [].
"""

import logging

import requests

log = logging.getLogger(__name__)

_BASE = "https://musicbrainz.org/ws/2"
_UA = "BPM-Tagger/2.1 (https://github.com/PauloJf/BPM-Tagger)"


def _artist_credit(rec: dict) -> str:
    parts = []
    for a in rec.get("artist-credit") or []:
        if isinstance(a, dict):
            name = a.get("name") or (a.get("artist") or {}).get("name") or ""
            if name:
                parts.append(name)
    return ", ".join(parts)


def lookup_isrcs(artist: str, title: str, limit: int = 5) -> list[dict]:
    """Return ISRC candidates [{source, isrc, title, artist, url}] for artist+title."""
    if not (artist or title):
        return []
    query = f'recording:"{title}" AND artist:"{artist}"'
    try:
        resp = requests.get(f"{_BASE}/recording",
                            params={"query": query, "fmt": "json", "limit": limit},
                            headers={"User-Agent": _UA}, timeout=10)
        resp.raise_for_status()
        recordings = resp.json().get("recordings", []) or []
    except Exception as exc:
        log.warning("MusicBrainz search failed: %s", exc)
        return []

    out: list[dict] = []
    # Fetch ISRCs for the top matches, stopping once we find some (keeps us
    # within MusicBrainz's rate limit — at most a few extra calls).
    for rec in recordings[:3]:
        mbid = rec.get("id")
        if not mbid:
            continue
        isrcs = rec.get("isrcs") or []
        if not isrcs:
            try:
                r2 = requests.get(f"{_BASE}/recording/{mbid}",
                                  params={"inc": "isrcs", "fmt": "json"},
                                  headers={"User-Agent": _UA}, timeout=10)
                r2.raise_for_status()
                isrcs = r2.json().get("isrcs", []) or []
            except Exception as exc:
                log.debug("MusicBrainz isrc lookup failed for %s: %s", mbid, exc)
                isrcs = []
        for code in isrcs:
            out.append({"source": "musicbrainz", "isrc": code,
                        "title": rec.get("title", ""), "artist": _artist_credit(rec),
                        "url": f"https://musicbrainz.org/recording/{mbid}"})
        if out:
            break
    return out
