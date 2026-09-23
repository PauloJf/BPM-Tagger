"""Text normalization for track/artist identity — pure stdlib, no I/O.

Lives outside ``grabber`` on purpose: the core (``db``, ``scan``) needs these
keys for the tag index and artist browsing, and must import without the
grabber's dependencies (``rapidfuzz``). ``grabber.matching`` re-exports every
name here, so existing imports keep working.
"""

import re
import unicodedata
from typing import Optional

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
