"""/api/stats — summary payload."""





def _login(client):
    resp = client.post("/api/login", json={"password": "s3cret"})
    assert resp.status_code == 200


def test_stats_has_no_grabber_section(client):
    _login(client)
    data = client.get("/api/stats").get_json()
    assert "summary" in data
    assert "grabber" not in data


def test_stats_top_plays(client):
    """Most-played leaderboards: top tracks, top artists (summed), total plays."""
    _login(client)
    db = client.application.extensions["state"].db

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

    data = client.get("/api/stats").get_json()
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


def test_most_played_pagination(client):
    """/api/stats returns the first PAGE_SIZE rows + a has-more flag;
    /api/stats/most_played pages through the rest without skips or repeats."""
    _login(client)
    db = client.application.extensions["state"].db

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

    data = client.get("/api/stats").get_json()
    assert len(data["top_tracks"]) == 15
    assert len(data["top_artists"]) == 15
    assert data["top_tracks_more"] is True
    assert data["top_artists_more"] is True

    page2 = client.get("/api/stats/most_played?kind=tracks&offset=15").get_json()
    assert [t["title"] for t in page2["items"]] == [f"Song {i:02d}" for i in range(15, 20)]
    assert page2["has_more"] is False

    artists2 = client.get("/api/stats/most_played?kind=artists&offset=15").get_json()
    assert [a["name"] for a in artists2["items"]] == [f"Artist {i:02d}" for i in range(15, 20)]
    assert artists2["has_more"] is False

    # First page + second page never overlap (deterministic ORDER BY).
    firsts = {t["file_path"] for t in data["top_tracks"]}
    assert firsts.isdisjoint({t["file_path"] for t in page2["items"]})


def test_most_played_rejects_bad_params(client):
    _login(client)
    assert client.get("/api/stats/most_played?kind=nope").status_code == 400
    assert client.get("/api/stats/most_played?kind=tracks&offset=x").status_code == 400
