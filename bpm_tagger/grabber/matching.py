"""Track normalization + fuzzy match scoring (§5).

Used both for library matching (does this Spotify track already exist on disk?)
and, from M4, for ranking download-provider candidates. Pure functions +
rapidfuzz; no I/O.
"""

from typing import Optional

from rapidfuzz import fuzz

# Normalizers live in bpm_tagger.text (stdlib-only, importable by the core);
# re-exported so every existing `from ...grabber.matching import X` still works.
from ..text import (  # noqa: F401
    _ARTIST_SPLIT,
    _CREDIT_SPLIT,
    _FEAT,
    _NAME_LIST_SPLIT,
    _NOISE,
    _NOISE_BRACKET,
    _NOISE_DASH,
    _PUNCT,
    _WS,
    _base_normalize,
    _strip_diacritics,
    extract_feat,
    normalize_artist,
    normalize_artist_name,
    normalize_title,
    split_artist_credits,
)

# Tokens that mark a different recording; penalized if present on one side only.
_VARIANT_TOKENS = ("live", "remix", "cover", "acoustic", "instrumental",
                   "karaoke", "sped up", "spedup", "slowed", "reverb", "demo")


def _variant_penalty(a_title: Optional[str], b_title: Optional[str]) -> float:
    a = (a_title or "").lower()
    b = (b_title or "").lower()
    for tok in _VARIANT_TOKENS:
        if (tok in a) != (tok in b):
            return 0.15
    return 0.0


def _duration_tier(a_ms: Optional[int], b_ms: Optional[int]) -> float:
    if not a_ms or not b_ms:
        return 0.35
    delta = abs(a_ms - b_ms) / 1000.0
    if delta <= 2:
        return 1.0
    if delta <= 5:
        return 0.6
    if delta <= 10:
        return 0.2
    return 0.0


def _album_bonus(a_album: Optional[str], b_album: Optional[str]) -> float:
    na, nb = _base_normalize(a_album), _base_normalize(b_album)
    if na and nb and (na == nb or fuzz.token_sort_ratio(na, nb) >= 90):
        return 1.0
    return 0.0


def score(a: dict, b: dict) -> tuple[float, dict]:
    """Score how likely two tracks are the same recording (0..1).

    ISRC exact match short-circuits to 1.0. A duration gap over 10s hard-blocks
    the auto-accept band (result capped below AUTO_ACCEPT) unless ISRC matched.
    Both dicts use keys: title, artist, album, duration_ms, isrc.
    """
    ai, bi = (a.get("isrc") or "").strip(), (b.get("isrc") or "").strip()
    if ai and bi and ai.upper() == bi.upper():
        return 1.0, {"isrc": True, "total": 1.0}

    title = fuzz.token_sort_ratio(normalize_title(a.get("title")), normalize_title(b.get("title"))) / 100.0
    artist = fuzz.token_set_ratio(normalize_artist(a.get("artist")), normalize_artist(b.get("artist"))) / 100.0
    dur = _duration_tier(a.get("duration_ms"), b.get("duration_ms"))
    album = _album_bonus(a.get("album"), b.get("album"))
    penalty = _variant_penalty(a.get("title"), b.get("title"))

    total = 0.40 * title + 0.30 * artist + 0.20 * dur + 0.10 * album - penalty
    total = max(0.0, min(1.0, total))

    # Duration hard-block: >10s apart can't auto-accept (unless ISRC, handled above).
    dur_delta = (abs(a.get("duration_ms", 0) - b.get("duration_ms", 0)) / 1000.0
                 if a.get("duration_ms") and b.get("duration_ms") else None)
    if dur_delta is not None and dur_delta > 10:
        total = min(total, 0.84)

    return total, {
        "title": round(title, 3), "artist": round(artist, 3),
        "duration_tier": dur, "album_bonus": album, "penalty": penalty,
        "total": round(total, 3),
    }


def library_match(sp: dict, db, threshold: float = 0.80) -> Optional[str]:
    """Return the file_path of a library track matching this Spotify track, or None.

    `sp` needs title/artist/album/duration_ms/isrc (+ norm_title/norm_artist for the
    SQL prefilter). A stamped spotify_track_id wins outright (a grabbed file we
    filed ourselves), then ISRC-equal, then the best fuzzy score must reach
    `threshold`.
    """
    sid = (sp.get("spotify_track_id") or "").strip()
    if sid:
        hits = db.find_by_spotify_id(sid)
        if hits:
            return hits[0]["file_path"]

    isrc = (sp.get("isrc") or "").strip()
    if isrc:
        hits = db.find_by_isrc(isrc)
        if hits:
            return hits[0]["file_path"]

    norm_artist = sp.get("norm_artist") or normalize_artist(sp.get("artist"))
    norm_title = sp.get("norm_title") or normalize_title(sp.get("title"))
    candidates = db.find_candidates_by_norm(norm_artist, norm_title)

    best_path, best_score = None, 0.0
    for cand in candidates:
        s, _ = score(sp, cand)
        if s > best_score:
            best_score, best_path = s, cand["file_path"]
    return best_path if best_score >= threshold else None
