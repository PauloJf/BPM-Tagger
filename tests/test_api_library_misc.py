"""Playlist delete, m3u export and the duplicates report: library features that used
to be tested through the grabber fixture, now against a plain app + Local playlist."""

import os

import pytest

from bpm_tagger import matching as m
from bpm_tagger.web.app import create_app


@pytest.fixture
def lib(tmp_path):
    config = {
        "db_path": str(tmp_path / "bpm.db"),
        "music_dir": str(tmp_path / "music"),
        "ui_password": "s3cret",
        "ui_secret_key": "k",
        "index_tags": True,
    }
    os.makedirs(config["music_dir"], exist_ok=True)
    app = create_app(config)
    app.config["TESTING"] = True
    st = app.extensions["state"]
    client = app.test_client()
    client.post("/api/login", json={"password": "s3cret"})
    client._csrf = client.get("/api/me").get_json()["csrf_token"]
    return client, st


def _add_track(st, name, title, artist):
    p = os.path.join(st.music_dir, name)
    with open(p, "wb") as fh:
        fh.write(b"\x00")
    st.db.upsert_track(p, "1:2", 120.0, None, None, 120.0, 0.9, "librosa", "done")
    st.db.update_track_tags(p, {"title": title, "artist": artist,
                                "norm_title": m.normalize_title(title),
                                "norm_artist": m.normalize_artist(artist)}, "1:2")
    return p


def test_delete_playlist(lib):
    client, st = lib
    pid = st.db.add_local_playlist("X")
    assert client.delete(f"/api/playlists/{pid}", headers={"X-CSRF-Token": client._csrf}).status_code == 200
    assert st.db.get_playlist(pid) is None


def test_export_m3u_lists_the_playlist_tracks(lib):
    client, st = lib
    path = _add_track(st, "bl.mp3", "Blinding Lights", "The Weeknd")
    pid = st.db.add_local_playlist("My Playlist")
    st.db.add_track_to_local_playlist(pid, path)
    r = client.get(f"/api/playlists/{pid}/export.m3u")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert body.startswith("#EXTM3U")
    assert "bl.mp3" in body
    assert 'filename="My_Playlist.m3u"' in r.headers.get("Content-Disposition", "")


def test_duplicates_report(lib):
    client, st = lib
    for name in ("dup_a.mp3", "dup_b.mp3"):
        _add_track(st, name, "Same Song", "Same Artist")
    groups = client.get("/api/duplicates").get_json()["groups"]
    dup = next((g for g in groups if g["title"] == "same song"), None)
    assert dup is not None and dup["count"] == 2
