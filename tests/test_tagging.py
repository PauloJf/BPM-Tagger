"""Cover art resize + embed/read roundtrip; tag-write mtime handling; last_change token."""

import io
import os

import numpy as np
import soundfile as sf
from mutagen.flac import FLAC
from PIL import Image

from bpm_tagger.db import BPMDatabase
from bpm_tagger.grabber.tagging import (embed_cover, read_cover, resize_cover,
                                        write_track_tags)


def _png(size):
    img = Image.new("RGB", (size, size), (120, 40, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_resize_cover_downscales_large():
    big = _png(2000)
    out = resize_cover(big, max_px=1200)
    img = Image.open(io.BytesIO(out))
    assert max(img.size) <= 1200
    assert img.format == "JPEG"


def test_resize_cover_leaves_small_untouched():
    small = _png(500)
    assert resize_cover(small, max_px=1200) is small


def test_embed_and_read_cover_flac(tmp_path):
    f = tmp_path / "song.flac"
    sr = 22050
    y = (0.1 * np.sin(2 * np.pi * 220 * np.arange(sr) / sr)).astype("float32")
    sf.write(str(f), y, sr, format="FLAC")
    jpeg = resize_cover(_png(2000))                    # >1200 → re-encoded to JPEG
    assert jpeg[:3] == b"\xff\xd8\xff"
    embed_cover(str(f), jpeg, mime="image/jpeg")
    assert FLAC(str(f)).pictures                       # embedded
    cover = read_cover(str(f))
    assert cover is not None and cover[0][:3] == b"\xff\xd8\xff"  # JPEG magic


def _flac(path):
    sr = 22050
    y = (0.1 * np.sin(2 * np.pi * 220 * np.arange(sr) / sr)).astype("float32")
    sf.write(str(path), y, sr, format="FLAC")
    # Backdate so any bump is unmistakable.
    old = os.stat(str(path)).st_mtime - 100_000
    os.utime(str(path), (old, old))
    return old


# PRESERVE_MTIME has to hold for UI metadata/cover edits too, not just BPM and
# lyrics writes — see issue #5.

def test_write_track_tags_preserves_mtime_by_default(tmp_path):
    f = tmp_path / "song.flac"
    old = _flac(f)
    assert write_track_tags(str(f), {"title": "T", "artist": "A"}) is None
    assert FLAC(str(f))["title"] == ["T"]
    assert os.stat(str(f)).st_mtime == old


def test_write_track_tags_bumps_mtime_when_disabled(tmp_path):
    f = tmp_path / "song.flac"
    old = _flac(f)
    assert write_track_tags(str(f), {"title": "T"}, preserve_mtime=False) is None
    assert os.stat(str(f)).st_mtime != old


def test_embed_cover_preserves_mtime_by_default(tmp_path):
    f = tmp_path / "song.flac"
    old = _flac(f)
    assert embed_cover(str(f), resize_cover(_png(2000))) is None
    assert read_cover(str(f)) is not None
    assert os.stat(str(f)).st_mtime == old


def test_embed_cover_bumps_mtime_when_disabled(tmp_path):
    f = tmp_path / "song.flac"
    old = _flac(f)
    assert embed_cover(str(f), resize_cover(_png(2000)), preserve_mtime=False) is None
    assert os.stat(str(f)).st_mtime != old


def test_last_change_updates_on_enqueue(tmp_path):
    db = BPMDatabase(str(tmp_path / "bpm.db"))
    before = db.get_last_change()
    db.enqueue_grab({"spotify_track_id": "s1", "title": "X"})
    assert db.get_last_change() != before
