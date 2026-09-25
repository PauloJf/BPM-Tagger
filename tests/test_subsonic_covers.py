"""getCoverArt's cached, capped cover pipeline (web/subsonic/covers.py)."""

import io
import os
import threading

import numpy as np
import pytest
import soundfile as sf
from mutagen.flac import FLAC, Picture
from PIL import Image

from bpm_tagger.web.subsonic import covers


def _jpeg(px=1200):
    img = Image.fromarray((np.random.default_rng(0).random((px, px, 3)) * 255).astype("uint8"))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=90)
    return out.getvalue()


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setattr(covers, "_no_art", {})
    path = tmp_path / "music" / "a.flac"
    path.parent.mkdir()
    sf.write(str(path), np.zeros(2205, dtype="float32"), 22050, format="FLAC")
    f = FLAC(str(path))
    pic = Picture()
    pic.data, pic.mime, pic.type = _jpeg(), "image/jpeg", 3
    f.add_picture(pic)
    f.save()
    config = {"db_path": str(tmp_path / "data" / "bpm.db")}
    track = {"file_path": str(path), "file_hash": "h1"}
    return {"config": config, "track": track, "path": path}


def _size(data):
    return max(Image.open(io.BytesIO(data)).size)


def test_resizes_and_caches(lib, monkeypatch):
    data, mime = covers.cover_for(lib["config"], [lib["track"]], 300)
    assert mime == "image/jpeg" and _size(data) <= 300
    files = os.listdir(covers.cache_dir(lib["config"]))
    assert len(files) == 1 and files[0].endswith(".jpg")

    # A second request is served from disk: the source is never re-read.
    monkeypatch.setattr(covers, "_source", lambda p: pytest.fail("re-read the source"))
    again, _ = covers.cover_for(lib["config"], [lib["track"]], 300)
    assert again == data


def test_a_changed_file_invalidates_the_cache(lib):
    covers.cover_for(lib["config"], [lib["track"]], 300)
    covers.cover_for(lib["config"], [{**lib["track"], "file_hash": "h2"}], 300)
    assert len(os.listdir(covers.cache_dir(lib["config"]))) == 2


def test_busy_render_slots_serve_the_original(lib, monkeypatch):
    monkeypatch.setattr(covers, "_render_slots", threading.BoundedSemaphore(1))
    covers._render_slots.acquire()   # every slot taken
    data, _ = covers.cover_for(lib["config"], [lib["track"]], 300)
    assert _size(data) == 1200      # original, not resized
    assert not os.path.exists(covers.cache_dir(lib["config"]))


def test_no_size_serves_the_original(lib):
    data, _ = covers.cover_for(lib["config"], [lib["track"]], None)
    assert _size(data) == 1200


def test_small_art_is_not_upscaled_or_cached(lib):
    data, _ = covers.cover_for(lib["config"], [lib["track"]], 2000)
    assert _size(data) == 1200
    assert not os.path.exists(covers.cache_dir(lib["config"]))


def test_no_art_is_remembered(lib, tmp_path, monkeypatch):
    bare = tmp_path / "music" / "bare.flac"
    sf.write(str(bare), np.zeros(2205, dtype="float32"), 22050, format="FLAC")
    track = {"file_path": str(bare), "file_hash": "x"}
    calls = []
    real = covers._source
    monkeypatch.setattr(covers, "_source", lambda p: calls.append(p) or real(p))
    assert covers.cover_for(lib["config"], [track], 300) is None
    assert covers.cover_for(lib["config"], [track], 300) is None
    assert len(calls) == 1


def test_falls_through_to_the_next_track_with_art(lib, tmp_path):
    bare = tmp_path / "music" / "none.flac"
    sf.write(str(bare), np.zeros(2205, dtype="float32"), 22050, format="FLAC")
    data, _ = covers.cover_for(lib["config"], [{"file_path": str(bare), "file_hash": "x"},
                                               lib["track"]], 200)
    assert _size(data) <= 200


def test_resize_decodes_jpeg_at_reduced_scale(monkeypatch):
    """draft() is what makes a cold grid affordable on a NAS."""
    from PIL import JpegImagePlugin   # JPEG images override Image.draft
    called = []
    orig = JpegImagePlugin.JpegImageFile.draft

    def spy(self, mode, size):
        called.append(size)
        return orig(self, mode, size)
    monkeypatch.setattr(JpegImagePlugin.JpegImageFile, "draft", spy)
    assert _size(covers._resize(_jpeg(1600), 200)) <= 200
    assert called == [(200, 200)]
