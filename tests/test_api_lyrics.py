"""Lyrics API: get/fetch/save endpoints + DB state + watcher anti-loop."""

import numpy as np
import pytest
import soundfile as sf
from mutagen.flac import FLAC

import bpm_tagger.web.api.lyrics as lyrics_mod
from bpm_tagger.bpm.lyrics import sidecar_path
from bpm_tagger.bpm.tags import get_file_hash
from bpm_tagger.web.app import create_app

LRC = "[00:12.00] First line\n[00:15.30] Second line"


@pytest.fixture
def lyr(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    config = {
        "db_path": str(tmp_path / "bpm.db"),
        "music_dir": str(music),
        "ui_password": "s3cret", "ui_secret_key": "k",
        "lyrics_mode": "embed",
    }
    app = create_app(config)
    app.config["TESTING"] = True
    st = app.extensions["state"]
    client = app.test_client()
    client.post("/api/login", json={"password": "s3cret"})
    client._csrf = client.get("/api/me").get_json()["csrf_token"]
    return client, st, music


def _track(st, music, name="song.flac", **tags):
    path = music / name
    sr = 22050
    y = (0.1 * np.sin(2 * np.pi * 220 * np.arange(sr) / sr)).astype("float32")
    sf.write(str(path), y, sr, format="FLAC")
    st.db.upsert_track(str(path), get_file_hash(str(path)), 120.0, None, None,
                       120.0, 0.9, "librosa", "done")
    full = {"title": "SOS", "artist": "ABBA", "album": "ABBA",
            "duration_ms": 200_000, **tags}
    st.db.update_track_tags(str(path), full, get_file_hash(str(path)))
    return str(path)


def test_lyrics_get_requires_login(lyr):
    client, st, music = lyr
    path = _track(st, music)
    fresh = client.application.test_client()
    assert fresh.get(f"/api/track/lyrics?path={path}").status_code == 401


def test_lyrics_get_none(lyr):
    client, st, music = lyr
    path = _track(st, music)
    r = client.get(f"/api/track/lyrics?path={path}")
    assert r.status_code == 200
    assert r.get_json() == {"lyrics": "", "synced": False, "source": "none", "status": ""}


def test_lyrics_fetch_embeds_and_indexes(lyr, monkeypatch):
    client, st, music = lyr
    path = _track(st, music)
    monkeypatch.setattr(lyrics_mod, "fetch_lyrics",
                        lambda *a, **kw: {"plain": "First line", "synced": LRC,
                                          "instrumental": False})

    r = client.post("/api/track/lyrics/fetch", json={"file_path": path},
                    headers={"X-CSRF-Token": client._csrf})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    # Synced version preferred, embedded in the tag.
    assert FLAC(path)["LYRICS"] == [LRC]
    row = st.db.get_track(path)
    assert row["lyrics_status"] == "fetched" and row["lyrics_synced"] == 1
    # Watcher anti-loop: the DB hash matches the rewritten file.
    assert st.db.needs_analysis(path, get_file_hash(path)) is False
    # GET now returns the embedded lyrics.
    g = client.get(f"/api/track/lyrics?path={path}").get_json()
    assert g["lyrics"] == LRC and g["synced"] is True and g["source"] == "embedded"


def test_lyrics_fetch_not_found_recorded(lyr, monkeypatch):
    client, st, music = lyr
    path = _track(st, music)
    monkeypatch.setattr(lyrics_mod, "fetch_lyrics", lambda *a, **kw: None)

    r = client.post("/api/track/lyrics/fetch", json={"file_path": path},
                    headers={"X-CSRF-Token": client._csrf})
    assert r.get_json()["ok"] is False
    assert st.db.get_track(path)["lyrics_status"] == "not_found"
    # not_found tracks drop out of the default bulk work list…
    assert st.db.get_tracks_missing_lyrics() == []
    # …but reappear when retrying not-found.
    assert [t["file_path"] for t in st.db.get_tracks_missing_lyrics(retry_not_found=True)] == [path]


def test_lyrics_fetch_requires_tags(lyr):
    client, st, music = lyr
    path = _track(st, music, title=None, artist=None)
    r = client.post("/api/track/lyrics/fetch", json={"file_path": path},
                    headers={"X-CSRF-Token": client._csrf})
    assert r.get_json()["ok"] is False


def test_lyrics_manual_save_and_remove(lyr):
    client, st, music = lyr
    path = _track(st, music)
    r = client.put("/api/track/lyrics", json={"file_path": path, "lyrics": "la la la"},
                   headers={"X-CSRF-Token": client._csrf})
    assert r.get_json() == {"ok": True, "synced": False}
    assert st.db.get_track(path)["lyrics_status"] == "embedded"

    r = client.put("/api/track/lyrics", json={"file_path": path, "lyrics": ""},
                   headers={"X-CSRF-Token": client._csrf})
    assert r.get_json()["ok"] is True
    assert "LYRICS" not in FLAC(path)
    assert st.db.get_track(path)["lyrics_status"] is None


def test_lyrics_sidecar_mode(lyr, monkeypatch):
    client, st, music = lyr
    st.config["lyrics_mode"] = "sidecar"
    path = _track(st, music)
    monkeypatch.setattr(lyrics_mod, "fetch_lyrics",
                        lambda *a, **kw: {"plain": "", "synced": LRC, "instrumental": False})

    r = client.post("/api/track/lyrics/fetch", json={"file_path": path},
                    headers={"X-CSRF-Token": client._csrf})
    assert r.get_json()["ok"] is True
    import os
    assert os.path.isfile(sidecar_path(path))
    assert "LYRICS" not in FLAC(path)  # tag untouched in sidecar mode
    g = client.get(f"/api/track/lyrics?path={path}").get_json()
    assert g["source"] == "sidecar" and g["synced"] is True


def test_lyrics_fill_status_shape(lyr):
    client, _st, _music = lyr
    r = client.get("/api/lyrics/fill/status")
    assert r.status_code == 200
    body = r.get_json()
    assert set(body) == {"running", "total", "done", "filled", "not_found"}
