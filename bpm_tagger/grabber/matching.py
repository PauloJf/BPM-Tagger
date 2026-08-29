"""Track normalization + fuzzy match scoring (§5).

Used both for library matching (does this Spotify track already exist on disk?)
and, from M4, for ranking download-provider candidates. Pure functions +
rapidfuzz; no I/O.
"""

import re
import unicodedata
from typing import Optional

from rapidfuzz import fuzz

# Bracketed / suffix "edition noise" that shouldn't affect identity. NOTE: we do
# NOT strip live/remix/acoustic/cover here — those change the recording and are
# handled as scoring penalties instead.
_NOISE = (
    r"remaster(ed)?|deluxe|expanded|anniversary|reissue|re-?issue|bonus|"
    r"mono|stereo|digital remaster|remastered version|single version|"
    r"album version|radio version|radio edit|original mix|original version|"
    r"\d{4} remaster|\d{4} version"
)
_NOISE_BRACKET = re.compile(r"[\(\[\{]\s*[^)\]\}]*(?:" + _NOISE + r")[^)\]\}]*[\)\]\}]", re.I)
_NOISE_DASH = re.compile(r"\s[-–—]\s.*(?:" + _NOISE + r").*$", re.I)
_FEAT = re.compile(r"\b(feat\.?|ft\.?|featuring|with)\b", re.I)
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")

# Splits a list of names on real separators only. Punctuation tokens (,&/)
# can't land mid-word, but the word tokens ('and'/'x') MUST be \b-bounded —
# unguarded, they also match as substrings ("x" inside "Axwell", "and" inside
# "Andrew"), silently cutting the name in half. Found via real library data:
# normalize_artist("Supermode, Axwell, Steve Angello") produced the token bag
# "a steve angello supermode well" — "Axwell" split into "A" + "well".
_NAME_LIST_SPLIT = re.compile(r"\s*(?:,|&|/|\band\b|\bx\b)\s*", re.I)

# Same fix, for normalize_artist()'s full split below: adds ';' and the
# feat/ft/featuring/with separators (already \b-bounded, matching _FEAT).
_ARTIST_SPLIT = re.compile(
    r"\s*(?:,|&|/|;|\bfeat\.?\b|\bft\.?\b|\bfeaturing\b|\bwith\b|\band\b|\bx\b)\s*", re.I)

# Tokens that mark a different recording; penalized if present on one side only.
_VARIANT_TOKENS = ("live", "remix", "cover", "acoustic", "instrumental",
                   "karaoke", "sped up", "spedup", "slowed", "reverb", "demo")


def _strip_diacritics(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _base_normalize(s: Optional[str]) -> str:
    if not s:
        return ""
    s = _strip_diacritics(s)
    s = s.lower()
    s = s.replace("&", " and ")
    s = _NOISE_BRACKET.sub(" ", s)
    s = _NOISE_DASH.sub(" ", s)
    s = _PUNCT.sub(" ", s)
    return _WS.sub(" ", s).strip()


def extract_feat(text: Optional[str]) -> tuple[str, list[str]]:
    """Split a 'Title (feat. X & Y)' / 'Artist ft. Z' string into (base, [feats])."""
    if not text:
        return "", []
    # Strip a trailing bracketed feat clause first: "Song (feat. X)"
    feats: list[str] = []
    bracket = re.search(r"[\(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s+([^)\]]+)[\)\]]", text, re.I)
    if bracket:
        feats += _NAME_LIST_SPLIT.split(bracket.group(1).strip())
        text = text[: bracket.start()] + text[bracket.end():]
    # Then an inline feat clause: "Artist feat. X, Y"
    m = _FEAT.search(text)
    if m:
        feats += _NAME_LIST_SPLIT.split(text[m.end():].strip())
        text = text[: m.start()]
    feats = [f.strip() for f in feats if f and f.strip()]
    return text.strip(), feats


def normalize_title(title: Optional[str]) -> str:
    base, _ = extract_feat(title)
    return _base_normalize(base)


# Conservative on purpose, unlike normalize_artist()'s aggressive fuzzy-match
# splitting below: only ','/';'/'/' reliably mark separate artist credits.
# '&'/'x'/'and' routinely appear inside real act names ("Chase & Status",
# "Dimitri Vegas & Like Mike"), so splitting on them would break those apart.
_CREDIT_SPLIT = re.compile(r"\s*[,;/]\s*")


def split_artist_credits(artist: Optional[str]) -> list[str]:
    """Split a multi-artist credit string ("Argy, SOLANCE") into individual
    artist names, for library browsing/linking (each credited artist gets
    their own page). Not for fuzzy matching — see normalize_artist()."""
    if not artist:
        return []
    return [p for p in (s.strip() for s in _CREDIT_SPLIT.split(artist)) if p]


def normalize_artist_name(name: Optional[str]) -> str:
    """Casing/diacritics-insensitive key for a single (already-split) artist
    name, used to group split credits for browsing. Unlike normalize_artist(),
    this never splits further — the caller has already isolated one artist."""
    return _base_normalize(name)


def normalize_artist(artist: Optional[str]) -> str:
    """Normalize an artist string into a canonical token bag incl. featured names."""
    base, feats = extract_feat(artist)
    parts = _ARTIST_SPLIT.split(base)
    parts = [p for p in parts if p]
    tokens = sorted({_base_normalize(p) for p in (parts + feats) if _base_normalize(p)})
    return " ".join(tokens)


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
