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


def test_stats_top_plays(grabber_client):
    """Most-played leaderboards: top tracks, top artists (summed), total plays."""
    _login(grabber_client)
    db = grabber_client.application.extensions["state"].db

    def seed(fp, title, artist, plays):
        db.upsert_track(fp, "h", 120.0, None, None, 120.0, 0.9, "librosa", "done")
        db.update_track_tags(fp, {
            "title": title, "artist": artist, "album": "Alb", "album_artist": artist,
            "track_no": 1, "disc_no": 1, "year": 2020, "isrc": "", "duration_ms": 200000,
            "norm_title": title.lower(), "norm_artist": artist.lower(),
        }, "h")
        db.set_play_counts([(fp, plays, None, None)])

    seed("/m/x.mp3", "Song X", "Alpha", 50)
    seed("/m/y.mp3", "Song Y", "Alpha", 30)
    seed("/m/z.mp3", "Song Z", "Beta", 40)

    data = grabber_client.get("/api/stats").get_json()
    assert data["total_plays"] == 120
    # Top track is the highest single play count.
    assert data["top_tracks"][0]["title"] == "Song X"
    assert data["top_tracks"][0]["play_count"] == 50
    # Top artist is by summed plays: Alpha (50+30=80) ahead of Beta (40).
    assert data["top_artists"][0] == {"name": "Alpha", "plays": 80, "tracks": 2}
    assert data["top_artists"][1]["name"] == "Beta"


def test_stats_top_plays_empty_without_play_data(client):
    _login(client)
    data = client.get("/api/stats").get_json()
    assert data["top_tracks"] == []
    assert data["top_artists"] == []
    assert data["total_plays"] == 0


def test_most_played_pagination(grabber_client):
    """/api/stats returns the first PAGE_SIZE rows + a has-more flag;
    /api/stats/most_played pages through the rest without skips or repeats."""
    _login(grabber_client)
    db = grabber_client.application.extensions["state"].db

    for i in range(20):  # 20 played tracks, distinct artists, descending plays
        fp = f"/m/t{i:02d}.mp3"
        db.upsert_track(fp, "h", 120.0, None, None, 120.0, 0.9, "librosa", "done")
        db.update_track_tags(fp, {
            "title": f"Song {i:02d}", "artist": f"Artist {i:02d}", "album": "Alb",
            "album_artist": f"Artist {i:02d}", "track_no": 1, "disc_no": 1, "year": 2020,
            "isrc": "", "duration_ms": 200000,
            "norm_title": f"song {i:02d}", "norm_artist": f"artist {i:02d}",
        }, "h")
        db.set_play_counts([(fp, 100 - i, None, None)])

    data = grabber_client.get("/api/stats").get_json()
    assert len(data["top_tracks"]) == 15
    assert len(data["top_artists"]) == 15
    assert data["top_tracks_more"] is True
    assert data["top_artists_more"] is True

    page2 = grabber_client.get("/api/stats/most_played?kind=tracks&offset=15").get_json()
    assert [t["title"] for t in page2["items"]] == [f"Song {i:02d}" for i in range(15, 20)]
    assert page2["has_more"] is False

    artists2 = grabber_client.get("/api/stats/most_played?kind=artists&offset=15").get_json()
    assert [a["name"] for a in artists2["items"]] == [f"Artist {i:02d}" for i in range(15, 20)]
    assert artists2["has_more"] is False

    # First page + second page never overlap (deterministic ORDER BY).
    firsts = {t["file_path"] for t in data["top_tracks"]}
    assert firsts.isdisjoint({t["file_path"] for t in page2["items"]})


def test_most_played_rejects_bad_params(client):
    _login(client)
    assert client.get("/api/stats/most_played?kind=nope").status_code == 400
    assert client.get("/api/stats/most_played?kind=tracks&offset=x").status_code == 400
