"""Image endpoints: search (Deezer), custom artist images, album/track covers."""

import os

import pytest
from mutagen.id3 import ID3

import bpm_tagger.web.api.images as images_mod
from bpm_tagger.bpm.tags import get_file_hash
from bpm_tagger.web.app import create_app

JPEG = b"\xff\xd8\xff\xe0" + b"fakejpegbytes" * 4


@pytest.fixture
def imgs(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    config = {
        "db_path": str(tmp_path / "bpm.db"),
        "music_dir": str(music),
        "ui_password": "s3cret", "ui_secret_key": "k",
    }
    app = create_app(config)
    app.config["TESTING"] = True
    st = app.extensions["state"]
    client = app.test_client()
    client.post("/api/login", json={"password": "s3cret"})
    client._csrf = client.get("/api/me").get_json()["csrf_token"]
    return client, st, music


def _mp3(st, music, name, **tags):
    """A bare-ID3 'mp3' registered in the DB with album tags."""
    path = music / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    ID3().save(str(path))  # give it an ID3 header so cover embeds work
    st.db.upsert_track(str(path), get_file_hash(str(path)), 120.0, None, None,
                       120.0, 0.9, "librosa", "done")
    st.db.update_track_tags(str(path), tags, get_file_hash(str(path)))
    return str(path)


class _Raw:
    def __init__(self, data):
        self._data = data

    def read(self, amt=None, decode_content=None):
        return self._data


class _FakeResp:
    def __init__(self, json_data=None, content=b""):
        self._json = json_data
        self.content = content
        self.raw = _Raw(content)

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


# ── search ────────────────────────────────────────────────────────────────────

def test_images_search_requires_login(imgs):
    client, _st, _music = imgs
    fresh = client.application.test_client()
    assert fresh.get("/api/images/search?kind=artist&q=abba").status_code == 401


def test_images_search_deezer_artists(imgs, monkeypatch):
    client, _st, _music = imgs

    def fake_get(url, **kw):
        assert "api.deezer.com/search/artist" in url
        return _FakeResp(json_data={"data": [
            {"name": "Daft Punk", "picture_xl": "https://cdn.dzcdn.net/dp.jpg",
             "picture_medium": "https://cdn.dzcdn.net/dp-med.jpg"},
            # Deezer placeholder image → must be skipped.
            {"name": "Ghost", "picture_xl": "https://cdn.dzcdn.net/images/artist//500x500.jpg"},
        ]})

    monkeypatch.setattr(images_mod.requests, "get", fake_get)
    r = client.get("/api/images/search?kind=artist&q=daft punk")
    assert r.status_code == 200
    cands = r.get_json()["candidates"]
    assert len(cands) == 1
    assert cands[0]["source"] == "deezer" and cands[0]["name"] == "Daft Punk"
    assert cands[0]["thumb_url"].endswith("dp-med.jpg")


def test_images_search_builds_query_from_fields(imgs, monkeypatch):
    client, _st, _music = imgs
    seen = {}

    def fake_get(url, params=None, **kw):
        seen["q"] = params["q"]
        return _FakeResp(json_data={"data": []})

    monkeypatch.setattr(images_mod.requests, "get", fake_get)
    r = client.get("/api/images/search?kind=album&album_artist=ABBA&album=Arrival")
    assert r.status_code == 200
    assert seen["q"] == "ABBA Arrival"


def test_images_search_invalid_kind(imgs):
    client, _st, _music = imgs
    assert client.get("/api/images/search?kind=nope&q=x").status_code == 400


# ── artist image ──────────────────────────────────────────────────────────────

def test_artist_image_set_from_url_then_serve_and_delete(imgs, monkeypatch):
    client, _st, _music = imgs
    monkeypatch.setattr(images_mod.requests, "get",
                        lambda url, **kw: _FakeResp(content=JPEG))

    r = client.post("/api/artist/image",
                    json={"name": "Daft Punk", "url": "https://cdn.example/x.jpg"},
                    headers={"X-CSRF-Token": client._csrf})
    assert r.get_json()["ok"] is True

    # Served with top priority by the artist-image endpoint (no fetch config on).
    g = client.get("/api/artist/image?name=Daft Punk")
    assert g.status_code == 200 and g.data == JPEG
    g.close()  # release the send_file handle so Windows allows the delete below

    d = client.delete("/api/artist/image", json={"name": "Daft Punk"},
                      headers={"X-CSRF-Token": client._csrf})
    assert d.get_json()["ok"] is True
    assert client.get("/api/artist/image?name=Daft Punk").status_code == 404


def test_artist_image_upload_raw(imgs):
    client, _st, _music = imgs
    r = client.put("/api/artist/image?name=ABBA", data=JPEG,
                   headers={"X-CSRF-Token": client._csrf})
    assert r.get_json()["ok"] is True
    g = client.get("/api/artist/image?name=ABBA")
    assert g.status_code == 200 and g.data == JPEG


def test_custom_artist_image_saved_to_library_when_enabled(imgs):
    client, st, music = imgs
    st.config["artist_images_to_library"] = True
    _mp3(st, music, os.path.join("ABBA", "Arrival", "one.mp3"),
         title="One", artist="ABBA", album_artist="ABBA")

    r = client.put("/api/artist/image?name=ABBA", data=JPEG,
                   headers={"X-CSRF-Token": client._csrf})
    body = r.get_json()
    assert body["ok"] is True
    expected = os.path.join(str(music), "ABBA", "artist.jpg")
    assert body["library_path"] == expected
    with open(expected, "rb") as f:
        assert f.read() == JPEG


def test_artist_image_custom_beats_local_file(imgs):
    client, st, music = imgs
    # Local artist.jpg exists…
    _mp3(st, music, os.path.join("ABBA", "Arrival", "one.mp3"),
         title="One", artist="ABBA", album_artist="ABBA")
    with open(music / "ABBA" / "artist.jpg", "wb") as f:
        f.write(b"\xff\xd8local")
    # …but an explicit custom image wins.
    client.put("/api/artist/image?name=ABBA", data=JPEG,
               headers={"X-CSRF-Token": client._csrf})
    g = client.get("/api/artist/image?name=ABBA")
    assert g.data == JPEG
    g.close()  # release the send_file handle so Windows allows the delete below
    # After deleting the custom image, the local file is served again.
    d = client.delete("/api/artist/image", json={"name": "ABBA"},
                      headers={"X-CSRF-Token": client._csrf})
    assert d.get_json()["ok"] is True
    assert client.get("/api/artist/image?name=ABBA").data == b"\xff\xd8local"


# ── album / track covers ──────────────────────────────────────────────────────

def test_album_cover_applies_to_all_tracks(imgs):
    client, st, music = imgs
    p1 = _mp3(st, music, "a1.mp3", title="One", album="Arrival", album_artist="ABBA")
    p2 = _mp3(st, music, "a2.mp3", title="Two", album="Arrival", album_artist="ABBA")
    _mp3(st, music, "other.mp3", title="X", album="Other", album_artist="ABBA")

    r = client.put("/api/album/cover?album=Arrival&album_artist=ABBA", data=JPEG,
                   headers={"X-CSRF-Token": client._csrf})
    body = r.get_json()
    assert body["ok"] is True and body["updated"] == 2 and body["failed"] == []
    for p in (p1, p2):
        apics = ID3(p).getall("APIC")
        assert apics and apics[0].data == JPEG
        # Watcher anti-loop: DB hash refreshed after embed.
        assert st.db.needs_analysis(p, get_file_hash(p)) is False
    # The other album was left alone.
    other = os.path.join(str(music), "other.mp3")
    assert not ID3(other).getall("APIC")


def test_album_cover_unknown_album_404(imgs):
    client, _st, _music = imgs
    r = client.put("/api/album/cover?album=Nope", data=JPEG,
                   headers={"X-CSRF-Token": client._csrf})
    assert r.status_code == 404


def test_track_cover_from_url(imgs, monkeypatch):
    client, st, music = imgs
    path = _mp3(st, music, "t.mp3", title="T", album="A", album_artist="AA")
    monkeypatch.setattr(images_mod.requests, "get",
                        lambda url, **kw: _FakeResp(content=JPEG))

    r = client.post("/api/track/cover",
                    json={"file_path": path, "url": "https://cdn.example/c.jpg"},
                    headers={"X-CSRF-Token": client._csrf})
    assert r.get_json()["ok"] is True
    apics = ID3(path).getall("APIC")
    assert apics and apics[0].data == JPEG
    assert st.db.needs_analysis(path, get_file_hash(path)) is False


def test_track_cover_rejects_non_http_url(imgs):
    client, st, music = imgs
    path = _mp3(st, music, "u.mp3", title="U")
    r = client.post("/api/track/cover",
                    json={"file_path": path, "url": "file:///etc/passwd"},
                    headers={"X-CSRF-Token": client._csrf})
    assert r.status_code == 400 and r.get_json()["ok"] is False
