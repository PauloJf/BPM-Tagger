"""/api/related/library — offline similar tracks from the library (web/similar.py),
shared by the Similar panel, Listen's similar radio and Subsonic getSimilarSongs."""

import os
import sqlite3

import pytest

from bpm_tagger.config import build_config


def _app(base_config, **over):
    from bpm_tagger.web.app import create_app
    cfg = build_config()
    cfg.update({"db_path": base_config["db_path"], "music_dir": base_config["music_dir"],
                "ui_password": "s3cret", "ui_secret_key": "unit-test-secret-key",
                "write_tags": False, "run_password": "runpw"})
    cfg.update(over)
    os.makedirs(cfg["music_dir"], exist_ok=True)
    app = create_app(cfg)
    app.config["TESTING"] = True
    return app


def _login(client, password="s3cret", username=""):
    client.post("/api/login", json={"password": password, "username": username})
    return {"X-CSRF-Token": client.get("/api/me").get_json()["csrf_token"]}


@pytest.fixture
def lib(base_config):
    app = _app(base_config)
    music, db_path = base_config["music_dir"], base_config["db_path"]
    paths = {}
    rows = [  # name, artist, bpm, disliked
        ("seed", "Ann", 170.0, 0), ("ann2", "Ann", 120.0, 0), ("ann3", "Ann, Bob", 100.0, 0),
        ("near", "Cy", 172.0, 0), ("half", "Dee", 86.0, 0), ("far", "Eve", 130.0, 0),
        ("hated", "Fay", 171.0, 1),
    ]
    conn = sqlite3.connect(db_path)
    for name, artist, bpm, disliked in rows:
        p = os.path.join(music, f"{name}.mp3")
        paths[name] = p
        tid = conn.execute(
            "INSERT INTO tracks (file_path, title, artist, bpm, status, disliked) "
            "VALUES (?, ?, ?, ?, 'done', ?)", (p, name, artist, bpm, disliked)).lastrowid
        for credit in [a.strip() for a in artist.split(",")]:
            conn.execute("INSERT INTO track_artists (track_id, name, norm_name) VALUES (?, ?, ?)",
                         (tid, credit, credit.lower()))
    conn.commit()
    conn.close()
    admin = app.test_client()
    csrf = _login(admin)
    return {"app": app, "admin": admin, "csrf": csrf, "paths": paths}


def _titles(resp):
    assert resp.status_code == 200, resp.get_json()
    return [(t["title"], t["reason"]) for t in resp.get_json()["tracks"]]


def test_same_artist_first_then_folded_tempo(lib):
    got = _titles(lib["admin"].get("/api/related/library",
                                   query_string={"path": lib["paths"]["seed"], "count": 10}))
    artist = [t for t, r in got if r == "artist"]
    tempo = [t for t, r in got if r == "tempo"]
    assert set(artist) == {"ann2", "ann3"}          # every credited artist, incl. "Ann, Bob"
    assert set(tempo) == {"near", "half"}           # 172 and 86 (→172) within 5 % of 170
    assert got.index(next(g for g in got if g[1] == "tempo")) >= 1
    names = [t for t, _ in got]
    assert "seed" not in names and "hated" not in names and "far" not in names


def test_run_target_centres_the_band_and_filters_artist_picks(lib):
    got = _titles(lib["admin"].get("/api/related/library", query_string={
        "path": lib["paths"]["seed"], "target": 125, "stretch_pct": 5}))
    # ann2 (120) fits 125±5 %; ann3 (100) doesn't; far (130) fits; near/half don't.
    assert sorted(t for t, _ in got) == ["ann2", "far"]


def test_post_excludes_recent_paths(lib):
    got = _titles(lib["admin"].post("/api/related/library", headers=lib["csrf"], json={
        "path": lib["paths"]["seed"], "exclude": [lib["paths"]["near"], lib["paths"]["ann2"]]}))
    assert "near" not in [t for t, _ in got] and "ann2" not in [t for t, _ in got]


def test_count_caps_the_list(lib):
    got = _titles(lib["admin"].get("/api/related/library",
                                   query_string={"path": lib["paths"]["seed"], "count": 2}))
    assert len(got) == 2


def test_unknown_path_is_empty(lib):
    assert _titles(lib["admin"].get("/api/related/library", query_string={"path": "/nope"})) == []


def test_player_gets_only_its_playlists_tracks(lib):
    st = lib["app"].extensions["state"]
    pid = st.db.add_local_playlist("mine")
    st.db.add_tracks_to_local_playlist(pid, [lib["paths"]["seed"], lib["paths"]["half"]])
    from werkzeug.security import generate_password_hash
    st.db.add_player("jog", generate_password_hash("pw12345"), playlist_ids=[pid])
    client = lib["app"].test_client()
    _login(client, "pw12345", "jog")
    got = _titles(client.get("/api/related/library", query_string={"path": lib["paths"]["seed"]}))
    assert [t for t, _ in got] == ["half"]


def test_guest_player_role_is_allowed(lib):
    client = lib["app"].test_client()
    _login(client, "runpw")
    r = client.get("/api/related/library", query_string={"path": lib["paths"]["seed"]})
    assert r.status_code == 200 and r.get_json()["tracks"]
