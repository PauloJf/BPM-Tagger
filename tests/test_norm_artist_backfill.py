"""One-time repair of stored norm_artist values written by the old, buggy
normalize_artist() (see bpm_tagger.grabber.matching) — it used to split on
bare 'x'/'and'/'ft'/'with' wherever they appeared, even mid-word, mangling
names like "Axwell" into "a"+"well". Existing rows carry the old mangled
value until this migration recomputes them once."""

import sqlite3

from bpm_tagger.db import BPMDatabase


def _old_style_db(tmp_path, rows):
    """A DB file as it would exist before this fix: `tracks`/`playlist_tracks`
    already populated, `norm_artist` holding the *old, buggy* value."""
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
            artist TEXT, norm_artist TEXT
        )
    """)
    for i, (artist, stale_norm_artist) in enumerate(rows):
        conn.execute(
            "INSERT INTO tracks (file_path, status, artist, norm_artist) "
            "VALUES (?, 'done', ?, ?)", (f"/m/{i}.mp3", artist, stale_norm_artist))
    conn.commit()
    conn.close()
    return db_path


def test_stale_norm_artist_is_recomputed_on_upgrade(tmp_path):
    # The pre-fix, mangled value for "Supermode, Axwell, Steve Angello".
    db_path = _old_style_db(tmp_path, [
        ("Supermode, Axwell, Steve Angello", "a steve angello supermode well"),
    ])

    db = BPMDatabase(db_path)
    row = db.get_track("/m/0.mp3")
    assert row["norm_artist"] == "axwell steve angello supermode"


def test_backfill_only_runs_once(tmp_path):
    """A second open (or a track added after the fix already ran) must not
    re-touch a norm_artist value written correctly by the fixed function —
    only the one-time migration pass is guarded, not every write."""
    db_path = _old_style_db(tmp_path, [("Axwell", "a well")])

    db = BPMDatabase(db_path)
    assert db.get_track("/m/0.mp3")["norm_artist"] == "axwell"

    # Re-opening must not error or redo work now that the marker is set.
    db2 = BPMDatabase(db_path)
    assert db2.get_track("/m/0.mp3")["norm_artist"] == "axwell"


def test_playlist_tracks_norm_artist_is_also_recomputed(tmp_path):
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
            locked         INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE playlists (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            source         TEXT NOT NULL DEFAULT 'spotify',
            spotify_id     TEXT UNIQUE,
            navidrome_id   TEXT UNIQUE,
            name           TEXT,
            description    TEXT DEFAULT '',
            pinned         INTEGER DEFAULT 0,
            snapshot_id    TEXT,
            enabled        INTEGER DEFAULT 1,
            image_url      TEXT,
            track_count    INTEGER DEFAULT 0,
            last_synced_at TEXT,
            created_at     TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE playlist_tracks (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            playlist_id      INTEGER NOT NULL,
            source_track_id  TEXT,
            spotify_track_id TEXT,
            position         INTEGER,
            title            TEXT,
            artist           TEXT,
            album            TEXT,
            album_artist     TEXT,
            duration_ms      INTEGER,
            isrc             TEXT,
            track_no         INTEGER,
            disc_no          INTEGER,
            year             INTEGER,
            cover_url        TEXT,
            added_at         TEXT,
            norm_title       TEXT,
            norm_artist      TEXT,
            match_status     TEXT DEFAULT 'unknown',
            matched_file_path TEXT,
            first_seen_at    TEXT,
            is_new           INTEGER DEFAULT 0,
            removed_at       TEXT,
            FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE
        )
    """)
    conn.execute("INSERT INTO playlists (spotify_id, name) VALUES ('p1', 'P')")
    conn.execute(
        "INSERT INTO playlist_tracks (playlist_id, artist, norm_artist) "
        "VALUES (1, 'Axwell', 'a well')")
    conn.commit()
    conn.close()

    db = BPMDatabase(db_path)
    with db._connect() as conn:
        row = conn.execute("SELECT norm_artist FROM playlist_tracks").fetchone()
    assert row["norm_artist"] == "axwell"
