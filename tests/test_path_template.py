"""Unit tests for grabber.path_template + transcode profile/arg logic."""

import os

from bpm_tagger.grabber import path_template as pt
from bpm_tagger.grabber import transcode as tc


def _meta(**kw):
    base = {"album_artist": "The Weeknd", "artist": "The Weeknd", "album": "After Hours",
            "title": "Blinding Lights", "track_no": 3, "disc_no": 1, "year": 2020}
    base.update(kw)
    return base


def test_render_default_template():
    out = pt.render("{AlbumArtist}/{Album}/{TrackNo:02d} - {Title}.{ext}", _meta(), "mp3")
    assert out == "The Weeknd/After Hours/03 - Blinding Lights.mp3"


def test_render_pads_track_number():
    out = pt.render("{TrackNo:02d} - {Title}.{ext}", _meta(track_no=5), "flac")
    assert out == "05 - Blinding Lights.flac"


def test_render_strips_illegal_chars_within_fields():
    out = pt.render("{Album}/{Title}.{ext}", _meta(album="AC/DC: Live?", title='a"b*c'), "mp3")
    # '/' and ':' and '?' and '"' and '*' replaced; separators preserved
    assert out == "AC DC Live/a b c.mp3"


def test_render_albumartist_falls_back_to_artist():
    out = pt.render("{AlbumArtist}/{Title}.{ext}", _meta(album_artist=None), "mp3")
    assert out.startswith("The Weeknd/")


def test_render_unknown_fields_default():
    out = pt.render("{AlbumArtist}/{Album}/{Title}.{ext}",
                    {"title": None, "artist": None, "album": None}, "mp3")
    assert out == "Unknown Artist/Unknown Album/Unknown Title.mp3"


def test_sanitize_segment_limits_length():
    seg = pt.sanitize_segment("x" * 300)
    assert len(seg) <= 180


def test_sanitize_segment_reserved_name():
    assert pt.sanitize_segment("CON") == "_CON"


def test_sanitize_trailing_dot_space():
    assert pt.sanitize_segment("name. ") == "name"


def test_unique_path_no_collision(tmp_path):
    p = pt.unique_path(str(tmp_path), "a/b.mp3")
    assert p == os.path.join(str(tmp_path), "a", "b.mp3")


def test_unique_path_suffixes_on_collision(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b.mp3").write_bytes(b"x")
    p = pt.unique_path(str(tmp_path), "a/b.mp3")
    assert p.endswith("b (2).mp3")
    (tmp_path / "a" / "b (2).mp3").write_bytes(b"x")
    p3 = pt.unique_path(str(tmp_path), "a/b.mp3")
    assert p3.endswith("b (3).mp3")


# ── transcode ─────────────────────────────────────────────────────────────────

def test_profile_ext():
    assert tc.profile_ext("mp3-320") == "mp3"
    assert tc.profile_ext("flac") == "flac"
    assert tc.profile_ext("opus-192") == "opus"
    assert tc.profile_ext("bogus") == "mp3"  # falls back


def test_ffmpeg_args_include_codec_and_bitrate():
    args = tc.build_ffmpeg_args("in.flac", "out.mp3", "mp3-320")
    assert args[0] == "ffmpeg"
    assert "libmp3lame" in args and "320k" in args
    assert args[-1] == "out.mp3"


def test_transcode_same_format_copies(tmp_path):
    src = tmp_path / "song.mp3"
    src.write_bytes(b"ID3fakecontent")
    dest, warn = tc.transcode(str(src), str(tmp_path / "out"), "mp3-320", "track")
    assert dest.endswith("track.mp3")
    assert warn is None
    assert os.path.exists(dest)  # copied, no ffmpeg needed
