"""track_artists — splitting multi-artist credits ("Argy, SOLANCE") so every
credited artist gets their own browsable page, not just an exact string match
on the full combo. Covers the split/normalize helpers, the write-time sync,
the artist index/page queries, and the one-time backfill migration."""

import sqlite3

import pytest

from bpm_tagger.db import BPMDatabase
from bpm_tagger.grabber.matching import normalize_artist_name, split_artist_credits


# ── split_artist_credits / normalize_artist_name ────────────────────────────

def test_split_on_comma_semicolon_slash():
    assert split_artist_credits("Argy, SOLANCE") == ["Argy", "SOLANCE"]
    assert split_artist_credits("Miss Monique; GENESI; Carl Bee") == \
        ["Miss Monique", "GENESI", "Carl Bee"]
    assert split_artist_credits("Teresa Salgueiro/Septeto De João Cristal") == \
        ["Teresa Salgueiro", "Septeto De João Cristal"]


def test_split_does_not_break_ampersand_act_names():
    # Real act names, not two collaborating solo artists — must stay intact.
    for name in ("Chase & Status", "Dimitri Vegas & Like Mike", "R & B Chartstars"):
        assert split_artist_credits(name) == [name]


def test_split_handles_empty_and_none():
    assert split_artist_credits(None) == []
    assert split_artist_credits("") == []
    assert split_artist_credits("Solo Artist") == ["Solo Artist"]


def test_normalize_artist_name_is_casing_and_diacritic_insensitive():
    assert normalize_artist_name("SOLANCE") == normalize_artist_name("solance")
    assert normalize_artist_name("Beyoncé") == normalize_artist_name("Beyonce")


# ── write-time sync ──────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    return BPMDatabase(str(tmp_path / "bpm.db"))


def _tag(db, path, artist, album_artist=None, title="T", album="A"):
    db.upsert_track(path, "h", 120.0, None, None, 120.0, 0.9, "librosa", "done")
    db.update_track_tags(path, {
        "title": title, "artist": artist, "album": album,
        "album_artist": album_artist if album_artist is not None else artist,
        "track_no": 1, "disc_no": 1, "year": 2020, "isrc": "", "duration_ms": 200000,
        "norm_title": title.lower(), "norm_artist": (artist or "").lower(),
    }, "h")


def test_multi_artist_track_links_to_every_credited_artist(db):
    _tag(db, "/m/collab.mp3", "Argy, SOLANCE", album_artist="Argy")

    argy = db.get_artist_tracks("Argy")
    solance = db.get_artist_tracks("SOLANCE")
    assert [r["file_path"] for r in argy] == ["/m/collab.mp3"]
    assert [r["file_path"] for r in solance] == ["/m/collab.mp3"]


def test_secondary_feature_gets_its_own_artist_page(db):
    """A guest who is never the album_artist (e.g. a feature on someone else's
    track) still gets a working page — the whole point of the fix."""
    _tag(db, "/m/hugel.mp3", "HUGEL, Topic, Arash", album_artist="HUGEL")

    topic = db.get_artist_tracks("Topic")
    arash = db.get_artist_tracks("Arash")
    assert [r["file_path"] for r in topic] == ["/m/hugel.mp3"]
    assert [r["file_path"] for r in arash] == ["/m/hugel.mp3"]


def test_re_tagging_drops_stale_artist_links(db):
    """Editing a track's credits away from an artist removes that link."""
    _tag(db, "/m/x.mp3", "Argy, SOLANCE", album_artist="Argy")
    assert len(db.get_artist_tracks("SOLANCE")) == 1

    _tag(db, "/m/x.mp3", "Argy", album_artist="Argy")  # re-tagged, SOLANCE dropped
    assert db.get_artist_tracks("SOLANCE") == []
    assert len(db.get_artist_tracks("Argy")) == 1


def test_artist_match_is_case_insensitive(db):
    _tag(db, "/m/y.mp3", "Daft Punk", album_artist="Daft Punk")
    assert len(db.get_artist_tracks("daft punk")) == 1


def test_list_artists_gives_every_credited_artist_their_own_row(db):
    _tag(db, "/m/collab.mp3", "Argy, SOLANCE", album_artist="Argy")
    _tag(db, "/m/solo.mp3", "Argy", album_artist="Argy")

    by_name = {a["name"]: a for a in db.list_artists()}
    assert set(by_name) == {"Argy", "SOLANCE"}
    assert by_name["Argy"]["tracks"] == 2
    assert by_name["SOLANCE"]["tracks"] == 1


# ── one-time backfill migration ──────────────────────────────────────────────

def test_existing_tracks_are_backfilled_on_upgrade(tmp_path):
    """A DB created before track_artists existed gets backfilled once, on the
    next BPMDatabase(...) open, without needing a re-scan."""
    db_path = str(tmp_path / "old.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE tracks (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path      TEXT UNIQUE NOT NULL,
            status         TEXT DEFAULT 'pending',
            error_message  TEXT,
            needs_review   INTEGER DEFAULT 0,
            reviewed       INTEGER DEFAULT 0,
            locked         INTEGER DEFAULT 0,
            artist TEXT, album_artist TEXT
        )
    """)
    conn.execute(
        "INSERT INTO tracks (file_path, status, artist, album_artist) "
        "VALUES ('/m/old.mp3', 'done', 'Argy, SOLANCE', 'Argy')")
    conn.commit()
    conn.close()

    db = BPMDatabase(db_path)
    assert [r["file_path"] for r in db.get_artist_tracks("SOLANCE")] == ["/m/old.mp3"]
    assert [r["file_path"] for r in db.get_artist_tracks("Argy")] == ["/m/old.mp3"]
