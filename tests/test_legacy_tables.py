"""The grabber's tables (grab_*, oauth_tokens, suggestions) are legacy: new databases
don't get them, and an existing database keeps them untouched (rollback safety, and so
the data can still be exported to Music Grabber)."""

import sqlite3

from bpm_tagger.db import BPMDatabase

LEGACY = ("grab_queue", "grab_candidates", "grab_events", "oauth_tokens",
          "suggestions", "suggestion_dismissed")
LIVE = ("tracks", "playlists", "playlist_tracks", "dismissed_dupes", "app_counters",
        "players", "runs", "track_ratings", "subsonic_credentials")


def _tables(path) -> set:
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def test_new_database_has_no_legacy_tables(tmp_path):
    path = str(tmp_path / "bpm.db")
    BPMDatabase(path)
    tables = _tables(path)
    assert not [t for t in LEGACY if t in tables]
    assert all(t in tables for t in LIVE)


def test_existing_database_keeps_its_legacy_tables_and_rows(tmp_path):
    path = str(tmp_path / "bpm.db")
    db = BPMDatabase(path)
    pid = db.add_playlist("37i9dQZF1DXcBWIGoYBM5M", "Old Spotify Mix")
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO playlist_tracks (playlist_id, spotify_track_id, position, "
                 "match_status) VALUES (?, 'sid0', 0, 'have')", (pid,))
    conn.execute("INSERT INTO playlist_tracks (playlist_id, spotify_track_id, position, "
                 "match_status) VALUES (?, 'sid1', 1, 'missing')", (pid,))
    # What a pre-detach release left behind, including a grab still "in flight" for a
    # track that is on the playlist.
    conn.execute("CREATE TABLE grab_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                 "spotify_track_id TEXT, title TEXT, status TEXT)")
    conn.execute("INSERT INTO grab_queue (spotify_track_id, title, status) "
                 "VALUES ('sid1', 'Pending Song', 'pending')")
    conn.execute("CREATE TABLE oauth_tokens (service TEXT UNIQUE NOT NULL, refresh_token TEXT)")
    conn.execute("INSERT INTO oauth_tokens VALUES ('spotify', 'r')")
    conn.commit()
    conn.close()

    db = BPMDatabase(path)          # reopen: migrations run again over the old shape

    conn = sqlite3.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM grab_queue").fetchone()[0] == 1
    assert conn.execute("SELECT refresh_token FROM oauth_tokens").fetchone()[0] == "r"
    conn.close()

    pl = db.get_playlist(pid, with_counts=True)
    assert (pl["have_count"], pl["missing_count"]) == (1, 1)
    assert pl["queued_count"] == 0          # nothing downloads here any more
    statuses = {t["spotify_track_id"]: t["derived_status"] for t in db.get_playlist_tracks(pid)}
    assert statuses == {"sid0": "have", "sid1": "missing"}
