"""/api/stats — summary payload and the grabber 'library sources' section."""

import os
import sqlite3

import pytest

from bpm_tagger.web.app import create_app


def _login(client):
    resp = client.post("/api/login", json={"password": "s3cret"})
    assert resp.status_code == 200


@pytest.fixture
def grabber_client(base_config):
    cfg = dict(base_config, grabber_enabled=True)
    os.makedirs(cfg["music_dir"], exist_ok=True)
    app = create_app(cfg)
    app.config["TESTING"] = True
    return app.test_client()


def _seed(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    # 2 grabber-managed tracks that duplicate each other, 1 pre-existing track.
    tracks = [
        ("/music/a/song.mp3", 1, "song", "artist"),
        ("/music/b/song.flac", 1, "song", "artist"),
        ("/music/c/other.mp3", 0, "other", "someone"),
    ]
    for fp, managed, nt, na in tracks:
        conn.execute(
            "INSERT INTO tracks (file_path, status, managed, norm_title, norm_artist, title, artist) "
            "VALUES (?, 'done', ?, ?, ?, ?, ?)",
            (fp, managed, nt, na, nt, na),
        )
    # Completed grabs per provider + one failed + one waiting in the inbox.
    for status, provider in [("done", "deezer"), ("done", "deezer"), ("done", "ytdlp"),
                             ("failed", "deezer"), ("awaiting_user", None)]:
        conn.execute(
            "INSERT INTO grab_queue (spotify_track_id, title, status, provider) "
            "VALUES (hex(randomblob(4)), 't', ?, ?)",
            (status, provider),
        )
    # One watched playlist: one track on disk, one missing.
    conn.execute(
        "INSERT INTO playlists (spotify_id, name, enabled, track_count) VALUES ('pl1', 'P', 1, 2)")
    pl_id = conn.execute("SELECT id FROM playlists").fetchone()[0]
    for pos, match in [(0, "have"), (1, "missing")]:
        conn.execute(
            "INSERT INTO playlist_tracks (playlist_id, spotify_track_id, position, match_status) "
            "VALUES (?, ?, ?, ?)",
            (pl_id, f"sid{pos}", pos, match),
        )
    conn.commit()
    conn.close()


def test_stats_has_no_grabber_section_when_disabled(client):
    _login(client)
    data = client.get("/api/stats").get_json()
    assert "summary" in data
    assert "grabber" not in data


def test_stats_grabber_section(grabber_client):
    _login(grabber_client)
    st = grabber_client.application.extensions["state"]
    _seed(st.config["db_path"])

    data = grabber_client.get("/api/stats").get_json()
    g = data["grabber"]

    assert g["managed"] == 2
    assert g["unmanaged"] == 1
    # Only completed grabs count toward the provider breakdown, most-used first.
    assert g["providers"] == [{"provider": "deezer", "count": 2},
                              {"provider": "ytdlp", "count": 1}]
    assert g["queue"]["done"] == 3
    assert g["queue"]["failed"] == 1
    assert g["queue"]["awaiting_user"] == 1
    assert g["duplicate_groups"] == 1
    assert g["duplicate_tracks"] == 2
    assert g["playlists"] == {"total": 1, "watched": 1, "have": 1, "missing": 1, "queued": 0}
