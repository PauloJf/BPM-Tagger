"""Metadata editor: PUT /api/track/tags rewrites + optionally renames, and the
edited file is NOT re-analyzed by the watcher (hash refreshed after write)."""

import os

import numpy as np
import pytest
import soundfile as sf
from mutagen.flac import FLAC

from bpm_tagger.bpm.tags import get_file_hash
from bpm_tagger.web.app import create_app


def _flac(path, **tags):
    sr = 22050
    y = (0.1 * np.sin(2 * np.pi * 220 * np.arange(sr) / sr)).astype("float32")
    sf.write(str(path), y, sr, format="FLAC")
    audio = FLAC(str(path))
    for k, v in tags.items():
        audio[k] = v
    audio.save()


@pytest.fixture
def editor(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    config = {
        "db_path": str(tmp_path / "bpm.db"),
        "music_dir": str(music),
        "ui_password": "s3cret", "ui_secret_key": "k",
        "path_template": "{AlbumArtist}/{Album}/{TrackNo:02d} - {Title}.{ext}",
    }
    app = create_app(config)
    st = app.extensions["state"]
    app.config["TESTING"] = True
    client = app.test_client()
    client.post("/api/login", json={"password": "s3cret"})
    client._csrf = client.get("/api/me").get_json()["csrf_token"]
    return client, st, music


def test_edit_tags_rewrites_and_renames(editor):
    client, st, music = editor
    src = music / "loose.flac"
    _flac(src, title="Old Title", artist="Old Artist")
    st.db.upsert_track(str(src), get_file_hash(str(src)), 120.0, None, None, 120.0, 0.9, "librosa", "done")

    r = client.put("/api/track/tags", json={
        "file_path": str(src), "title": "New Song", "artist": "New Artist",
        "album": "New Album", "album_artist": "New Artist", "track_no": 7,
        "apply_template": True,
    }, headers={"X-CSRF-Token": client._csrf})
    assert r.status_code == 200
    new_path = r.get_json()["file_path"]

    # File was renamed per the template and the tags were rewritten.
    assert new_path.replace("\\", "/").endswith("New Artist/New Album/07 - New Song.flac")
    assert os.path.exists(new_path) and not os.path.exists(str(src))
    assert FLAC(new_path)["title"] == ["New Song"]

    # DB row followed the move and carries the new normalized tags.
    row = st.db.get_track(new_path)
    assert row is not None and row["title"] == "New Song"
    assert row["norm_artist"] == "new artist"

    # Watcher anti-loop: the stored hash matches the file on disk → no re-analysis.
    assert st.db.needs_analysis(new_path, get_file_hash(new_path)) is False


def test_edit_tags_without_template_keeps_path(editor):
    client, st, music = editor
    src = music / "keep.flac"
    _flac(src, title="T", artist="A")
    st.db.upsert_track(str(src), get_file_hash(str(src)), 120.0, None, None, 120.0, 0.9, "librosa", "done")

    r = client.put("/api/track/tags", json={"file_path": str(src), "title": "Renamed Tag Only",
                                            "artist": "A"}, headers={"X-CSRF-Token": client._csrf})
    assert r.status_code == 200 and r.get_json()["file_path"] == str(src)
    assert FLAC(str(src))["title"] == ["Renamed Tag Only"]


def test_edit_tags_requires_csrf(editor):
    client, st, music = editor
    src = music / "x.flac"
    _flac(src, title="T", artist="A")
    st.db.upsert_track(str(src), get_file_hash(str(src)), 120.0, None, None, 120.0, 0.9, "librosa", "done")
    assert client.put("/api/track/tags", json={"file_path": str(src)}).status_code == 403
