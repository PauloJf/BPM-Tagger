"""Phase-1 playlists groundwork: the diff-based sync (new / removed / re-added
tombstone lifecycle) and the legacy-schema migration rebuild.

These exercise the DB layer directly — no web app or Spotify needed."""

import sqlite3

import pytest

from bpm_tagger.db import BPMDatabase


def _track(sid, title, position=0, **kw):
    """A minimal source track as sync_playlist_tracks() consumes it."""
    return {"source_track_id": sid, "spotify_track_id": sid, "position": position,
            "title": title, "artist": kw.get("artist", "A"), "album": "",
            "album_artist": "", "duration_ms": 200000, "isrc": "", "track_no": position,
            "disc_no": 1, "year": 2020, "cover_url": "", "added_at": "",
            "norm_title": title.lower(), "norm_artist": "a"}


@pytest.fixture
def db(tmp_path):
    return BPMDatabase(str(tmp_path / "bpm.db"))


def _by_title(db, pid):
    return {t["title"]: t for t in db.get_playlist_tracks(pid)}


# ── diff sync ──────────────────────────────────────────────────────────────

def test_initial_sync_marks_all_new(db):
    pid = db.add_playlist("PL1", "P")
    added, removed = db.sync_playlist_tracks(pid, [_track("a", "A", 0), _track("b", "B", 1)])
    assert (added, removed) == (2, 0)
    rows = _by_title(db, pid)
    assert set(rows) == {"A", "B"}
    assert all(r["is_new"] for r in rows.values())
    assert all(r["removed_at"] is None for r in rows.values())


def test_mark_seen_clears_new(db):
    pid = db.add_playlist("PL1", "P")
    db.sync_playlist_tracks(pid, [_track("a", "A")])
    db.mark_playlist_seen(pid)
    assert all(not r["is_new"] for r in _by_title(db, pid).values())


def test_removed_track_is_tombstoned_not_deleted(db):
    pid = db.add_playlist("PL1", "P")
    db.sync_playlist_tracks(pid, [_track("a", "A", 0), _track("b", "B", 1)])
    db.mark_playlist_seen(pid)

    added, removed = db.sync_playlist_tracks(pid, [_track("a", "A", 0), _track("c", "C", 1)])
    assert (added, removed) == (1, 1)

    rows = _by_title(db, pid)
    # B stays visible as a tombstone; C is the new one; A unchanged.
    assert rows["B"]["derived_status"] == "removed" and rows["B"]["removed_at"]
    assert rows["C"]["derived_status"] != "removed" and rows["C"]["is_new"]
    assert not rows["A"]["is_new"]

    # Live rows (matching / m3u) exclude the tombstone.
    live = {r["title"] for r in db.get_playlist_track_rows(pid)}
    assert live == {"A", "C"}

    # Coverage counts exclude the tombstone; new/removed surfaced separately.
    pl = next(p for p in db.list_playlists() if p["id"] == pid)
    assert pl["indexed_count"] == 2
    assert pl["new_count"] == 1 and pl["removed_count"] == 1


def test_tombstone_cleared_on_next_sync(db):
    pid = db.add_playlist("PL1", "P")
    db.sync_playlist_tracks(pid, [_track("a", "A"), _track("b", "B")])
    db.sync_playlist_tracks(pid, [_track("a", "A")])          # B tombstoned
    assert _by_title(db, pid)["B"]["derived_status"] == "removed"
    db.sync_playlist_tracks(pid, [_track("a", "A")])          # B still gone → purged
    assert "B" not in _by_title(db, pid)


def test_readded_track_revives_same_row(db):
    pid = db.add_playlist("PL1", "P")
    db.sync_playlist_tracks(pid, [_track("a", "A"), _track("b", "B")])
    orig_id = _by_title(db, pid)["B"]["id"]
    db.mark_playlist_seen(pid)

    db.sync_playlist_tracks(pid, [_track("a", "A")])          # B tombstoned
    db.sync_playlist_tracks(pid, [_track("a", "A"), _track("b", "B")])  # B re-added

    b = _by_title(db, pid)["B"]
    assert b["id"] == orig_id          # revived, not reinserted
    assert b["removed_at"] is None and b["is_new"]


def test_duplicate_source_ids_collapse(db):
    pid = db.add_playlist("PL1", "P")
    added, _ = db.sync_playlist_tracks(pid, [_track("a", "A", 0), _track("a", "A", 5)])
    assert added == 1
    assert len(db.get_playlist_track_rows(pid)) == 1


def test_match_state_preserved_across_sync(db):
    pid = db.add_playlist("PL1", "P")
    db.sync_playlist_tracks(pid, [_track("a", "A")])
    row = db.get_playlist_track_rows(pid)[0]
    db.set_playlist_track_match(row["id"], "have", "/music/a.mp3")

    db.sync_playlist_tracks(pid, [_track("a", "A", position=3)])   # metadata changes
    row2 = db.get_playlist_track_rows(pid)[0]
    assert row2["match_status"] == "have"
    assert row2["matched_file_path"] == "/music/a.mp3"
    assert row2["position"] == 3


# ── legacy migration ─────────────────────────────────────────────────────────

def _write_legacy_db(path):
    """A pre-generalization DB: Spotify-only playlists with NOT NULL spotify_id
    and no membership columns."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE playlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spotify_id TEXT UNIQUE NOT NULL, name TEXT, snapshot_id TEXT,
            enabled INTEGER DEFAULT 1, image_url TEXT, track_count INTEGER DEFAULT 0,
            last_synced_at TEXT, created_at TEXT);
        CREATE TABLE playlist_tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, playlist_id INTEGER NOT NULL,
            spotify_track_id TEXT, position INTEGER, title TEXT, artist TEXT, album TEXT,
            album_artist TEXT, duration_ms INTEGER, isrc TEXT, track_no INTEGER,
            disc_no INTEGER, year INTEGER, cover_url TEXT, added_at TEXT, norm_title TEXT,
            norm_artist TEXT, match_status TEXT DEFAULT 'unknown', matched_file_path TEXT,
            UNIQUE(playlist_id, position));
        INSERT INTO playlists (spotify_id, name, track_count) VALUES ('PLold', 'Legacy', 2);
        INSERT INTO playlist_tracks (playlist_id, spotify_track_id, position, title, match_status)
            VALUES (1, 's1', 0, 'One', 'have'), (1, 's2', 1, 'Two', 'missing');
    """)
    conn.commit()
    conn.close()


def test_migration_preserves_legacy_playlists(tmp_path):
    path = str(tmp_path / "legacy.db")
    _write_legacy_db(path)

    db = BPMDatabase(path)   # triggers the rebuild migration

    pl = db.get_playlist(1)
    assert pl["source"] == "spotify"
    assert pl["spotify_id"] == "PLold"
    assert pl["navidrome_id"] is None

    rows = {r["title"]: r for r in db.get_playlist_track_rows(1)}
    assert set(rows) == {"One", "Two"}
    # source_track_id backfilled from spotify_track_id; ids and match state preserved.
    assert rows["One"]["source_track_id"] == "s1"
    assert rows["One"]["id"] == 1 and rows["Two"]["id"] == 2
    assert rows["One"]["match_status"] == "have"
    assert rows["One"]["removed_at"] is None and rows["One"]["is_new"] == 0

    # The temporaries are gone and reopening is a clean no-op.
    tables = {r[0] for r in db._connect().execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "_playlists_old" not in tables and "_playlist_tracks_old" not in tables
    BPMDatabase(path)   # idempotent second open


def test_migration_relaxes_spotify_id_for_new_sources(tmp_path):
    """After migration a non-Spotify playlist (null spotify_id) can be inserted."""
    path = str(tmp_path / "legacy.db")
    _write_legacy_db(path)
    db = BPMDatabase(path)
    with db._connect() as conn:
        conn.execute(
            "INSERT INTO playlists (source, navidrome_id, name) VALUES ('navidrome', 'nd1', 'ND')")
        conn.commit()
    sources = sorted(p["source"] for p in db.list_playlists())
    assert sources == ["navidrome", "spotify"]
