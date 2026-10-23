"""Which artists the library holds, and how many tracks each has.

Shared by the Related panel (flagging suggested artists you already own) and the
artist pages. Pure: the only input is the DB's artist index rows.
"""

import re

from .matching import extract_feat, normalize_artist


def primary_artist(artist: str, album_artist: str) -> str:
    """The one artist a track counts towards: album_artist when present, else the
    first name in the (feat-stripped) artist string split on , / & / /."""
    aa = (album_artist or "").strip()
    if aa:
        return aa
    base, _ = extract_feat(artist or "")
    for part in re.split(r"\s*[,&/]\s*", base):
        if part.strip():
            return part.strip()
    return (artist or "").strip()


def _library_artists_from_rows(rows: list[dict]) -> dict:
    """normalize_artist(primary) → (display_name, track_count) over the given
    (artist, album_artist, starred) rows."""
    counts: dict[str, tuple[str, int]] = {}
    for r in rows:
        p = primary_artist(r.get("artist"), r.get("album_artist"))
        if not p:
            continue
        key = normalize_artist(p)
        if not key:
            continue
        disp, n = counts.get(key, (p, 0))
        counts[key] = (disp, n + 1)
    return counts


def build_library_artists(db) -> dict:
    """normalize_artist(primary) → (display_name, track_count) over the whole library."""
    return _library_artists_from_rows(db.get_artist_index_rows())
