"""Unit tests for grabber.matching — normalization, scoring, library match."""

import pytest

from bpm_tagger.grabber import matching as m


# ── Normalization ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("Björk", "bjork"),
    ("Song (2011 Remaster)", "song"),
    ("Track - Remastered 2009", "track"),
    ("Café del Mar", "cafe del mar"),
    ("AC/DC — Thunder", "ac dc thunder"),
    ("Money & Power", "money and power"),
    ("Song (Deluxe Edition)", "song"),
])
def test_normalize_title(raw, expected):
    assert m.normalize_title(raw) == expected


def test_feat_stripped_from_title():
    assert m.normalize_title("Blinding Lights (feat. Someone)") == "blinding lights"


def test_feat_extraction():
    base, feats = m.extract_feat("Song (feat. Drake & Future)")
    assert base.strip() == "Song"
    assert [f.lower() for f in feats] == ["drake", "future"]


def test_normalize_artist_includes_feat_sorted():
    # feat artist folded in; order-independent
    assert m.normalize_artist("Calvin Harris feat. Rihanna") == m.normalize_artist("Rihanna, Calvin Harris")


# ── Duration tiers (assert discrete tiers, not raw floats) ────────────────────

@pytest.mark.parametrize("a,b,tier", [
    (180000, 181000, 1.0),   # 1s
    (180000, 184000, 0.6),   # 4s
    (180000, 189000, 0.2),   # 9s
    (180000, 200000, 0.0),   # 20s
    (None, 180000, 0.35),    # unknown
])
def test_duration_tier(a, b, tier):
    assert m._duration_tier(a, b) == tier


# ── Scoring ───────────────────────────────────────────────────────────────────

def _t(title, artist, **kw):
    return {"title": title, "artist": artist, "album": kw.get("album"),
            "duration_ms": kw.get("duration_ms"), "isrc": kw.get("isrc")}


def test_isrc_exact_short_circuits_to_one():
    a = _t("Whatever", "X", isrc="USABC1234567", duration_ms=1000)
    b = _t("Totally Different", "Y", isrc="usabc1234567", duration_ms=999000)
    s, br = m.score(a, b)
    assert s == 1.0 and br["isrc"] is True


def test_identical_track_scores_high():
    a = _t("Blinding Lights", "The Weeknd", album="After Hours", duration_ms=200000)
    b = _t("Blinding Lights", "The Weeknd", album="After Hours", duration_ms=200500)
    s, _ = m.score(a, b)
    assert s >= 0.95


def test_live_variant_penalized_below_original():
    orig = _t("Song", "Band", duration_ms=180000)
    studio = _t("Song", "Band", duration_ms=180000)
    live = _t("Song (Live)", "Band", duration_ms=180000)
    assert m.score(orig, studio)[0] > m.score(orig, live)[0]


def test_remix_variant_penalized():
    orig = _t("Song", "Band", duration_ms=180000)
    remix = _t("Song (Kaytranada Remix)", "Band", duration_ms=180000)
    assert m.score(orig, remix)[1]["penalty"] == 0.15


def test_duration_hardblock_caps_auto_accept():
    # Same title/artist but >10s apart → capped below the 0.85 auto-accept line.
    a = _t("Song", "Band", album="A", duration_ms=180000)
    b = _t("Song", "Band", album="A", duration_ms=200000)
    s, _ = m.score(a, b)
    assert s <= 0.84


def test_remaster_suffix_matches_original():
    a = _t("Bohemian Rhapsody", "Queen", duration_ms=355000)
    b = _t("Bohemian Rhapsody - Remastered 2011", "Queen", duration_ms=355000)
    assert m.score(a, b)[0] >= 0.85  # stays in the auto-accept band


# ── Library match ─────────────────────────────────────────────────────────────

class _FakeDB:
    def __init__(self, rows):
        self.rows = rows

    def find_by_isrc(self, isrc):
        return [r for r in self.rows if (r.get("isrc") or "").upper() == isrc.upper()]

    def find_candidates_by_norm(self, na, nt):
        return [r for r in self.rows if r.get("norm_artist") == na or r.get("norm_title") == nt]


def _lib_row(path, title, artist, **kw):
    return {"file_path": path, "title": title, "artist": artist,
            "album": kw.get("album"), "duration_ms": kw.get("duration_ms"),
            "isrc": kw.get("isrc"),
            "norm_artist": m.normalize_artist(artist), "norm_title": m.normalize_title(title)}


def test_library_match_by_isrc():
    db = _FakeDB([_lib_row("/music/x.mp3", "A", "B", isrc="USxyz0000001")])
    sp = {"title": "Different", "artist": "Nobody", "isrc": "usxyz0000001"}
    assert m.library_match(sp, db) == "/music/x.mp3"


def test_library_match_by_fuzzy():
    db = _FakeDB([_lib_row("/music/bl.mp3", "Blinding Lights", "The Weeknd",
                           album="After Hours", duration_ms=200000)])
    sp = {"title": "Blinding Lights", "artist": "The Weeknd", "album": "After Hours",
          "duration_ms": 200000, "isrc": "",
          "norm_artist": m.normalize_artist("The Weeknd"),
          "norm_title": m.normalize_title("Blinding Lights")}
    assert m.library_match(sp, db) == "/music/bl.mp3"


def test_library_match_none_when_absent():
    db = _FakeDB([_lib_row("/music/other.mp3", "Some Other Song", "Another Artist",
                           duration_ms=180000)])
    sp = {"title": "Blinding Lights", "artist": "The Weeknd", "duration_ms": 200000,
          "isrc": "", "norm_artist": m.normalize_artist("The Weeknd"),
          "norm_title": m.normalize_title("Blinding Lights")}
    assert m.library_match(sp, db) is None
