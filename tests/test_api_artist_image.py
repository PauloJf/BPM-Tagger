"""/api/artist/image — local artist.jpg, opt-in online fetch, disk cache."""

import os
import sqlite3

import bpm_tagger.web.api.tracks as tracks_mod


def _login(client):
    resp = client.post("/api/login", json={"password": "s3cret"})
    assert resp.status_code == 200


def _insert_track(db_path: str, file_path: str, artist: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO tracks (file_path, artist, album_artist, status) VALUES (?, ?, ?, 'done')",
        (file_path, artist, artist))
    conn.commit()
    conn.close()


class _FakeResp:
    def __init__(self, json_data=None, content=b""):
        self._json = json_data
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


def test_artist_image_requires_login(client):
    assert client.get("/api/artist/image?name=ABBA").status_code == 401


def test_artist_image_local_file(client, base_config):
    _login(client)
    music = base_config["music_dir"]
    album_dir = os.path.join(music, "ABBA", "Arrival")
    os.makedirs(album_dir)
    _insert_track(base_config["db_path"], os.path.join(album_dir, "one.mp3"), "ABBA")
    # artist.jpg lives in the ARTIST folder (parent of the album dir).
    with open(os.path.join(music, "ABBA", "artist.jpg"), "wb") as f:
        f.write(b"\xff\xd8local-jpeg")

    resp = client.get("/api/artist/image?name=ABBA")
    assert resp.status_code == 200
    assert resp.data == b"\xff\xd8local-jpeg"


def test_artist_image_404_when_online_disabled(client):
    _login(client)
    assert client.get("/api/artist/image?name=Nobody").status_code == 404


def test_artist_image_online_fetch_then_cache(client, monkeypatch):
    _login(client)
    st = client.application.extensions["state"]
    st.config["fetch_artist_images"] = True

    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if "api.deezer.com" in url:
            return _FakeResp(json_data={"data": [
                {"name": "Daft Punk", "picture_xl": "https://cdn.example/img.jpg"}]})
        return _FakeResp(content=b"IMGBYTES")

    monkeypatch.setattr(tracks_mod.requests, "get", fake_get)

    resp = client.get("/api/artist/image?name=Daft Punk")
    assert resp.status_code == 200
    assert resp.data == b"IMGBYTES"
    assert len(calls) == 2  # search + image download

    # Second request is served from the disk cache — no further network calls.
    resp2 = client.get("/api/artist/image?name=Daft Punk")
    assert resp2.status_code == 200
    assert resp2.data == b"IMGBYTES"
    assert len(calls) == 2


def test_artist_image_wrong_match_rejected_and_miss_cached(client, monkeypatch):
    _login(client)
    st = client.application.extensions["state"]
    st.config["fetch_artist_images"] = True

    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        # Deezer returns its closest hit — a DIFFERENT artist.
        return _FakeResp(json_data={"data": [
            {"name": "Someone Else", "picture_xl": "https://cdn.example/other.jpg"}]})

    monkeypatch.setattr(tracks_mod.requests, "get", fake_get)

    assert client.get("/api/artist/image?name=Obscure Act").status_code == 404
    assert len(calls) == 1
    # The miss is remembered — no repeat lookup on the next page load.
    assert client.get("/api/artist/image?name=Obscure Act").status_code == 404
    assert len(calls) == 1
