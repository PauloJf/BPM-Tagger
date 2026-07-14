"""Characterization tests for write_bpm_tag and get_file_hash."""

import os
import wave

import numpy as np
import soundfile as sf
from mutagen.flac import FLAC
from mutagen.id3 import ID3
from mutagen.wave import WAVE

from bpm_tagger import get_file_hash, write_bpm_tag


def _write_flac(path):
    sr = 22050
    y = (0.1 * np.sin(2 * np.pi * 220 * np.arange(sr) / sr)).astype("float32")
    sf.write(str(path), y, sr, format="FLAC")


def _write_wav(path):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 8000)


def test_write_bpm_tag_flac_roundtrip(tmp_path):
    f = tmp_path / "song.flac"
    _write_flac(f)
    assert write_bpm_tag(str(f), 128.0) is True
    assert FLAC(str(f))["BPM"] == ["128"]


def test_write_bpm_tag_rounds_to_integer(tmp_path):
    f = tmp_path / "song.flac"
    _write_flac(f)
    write_bpm_tag(str(f), 128.6)
    assert FLAC(str(f))["BPM"] == ["129"]


def test_write_bpm_tag_mp3_writes_tbpm(tmp_path):
    # An empty .mp3 has no ID3 header; write_bpm_tag creates one and stores TBPM.
    f = tmp_path / "song.mp3"
    f.write_bytes(b"")
    assert write_bpm_tag(str(f), 140.0) is True
    assert ID3(str(f))["TBPM"].text == ["140"]


def test_write_bpm_tag_wav_writes_tbpm(tmp_path):
    # WAV exposes ID3 tags via mutagen; a fresh file has no ID3 chunk yet.
    f = tmp_path / "song.wav"
    _write_wav(f)
    assert write_bpm_tag(str(f), 120.0) is True
    assert WAVE(str(f)).tags["TBPM"].text == ["120"]


def test_write_bpm_tag_wav_overwrites_existing(tmp_path):
    f = tmp_path / "song.wav"
    _write_wav(f)
    assert write_bpm_tag(str(f), 120.0) is True
    assert write_bpm_tag(str(f), 175.0) is True
    tags = WAVE(str(f)).tags
    assert tags["TBPM"].text == ["175"]
    assert len(tags.getall("TBPM")) == 1


def test_write_bpm_tag_wav_preserves_mtime(tmp_path):
    f = tmp_path / "song.wav"
    _write_wav(f)
    old = os.stat(str(f)).st_mtime - 100_000
    os.utime(str(f), (old, old))
    assert write_bpm_tag(str(f), 120.0) is True
    assert os.stat(str(f)).st_mtime == old


def test_write_bpm_tag_unsupported_returns_false(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("not audio")
    assert write_bpm_tag(str(f), 120.0) is False


def test_write_bpm_tag_preserves_mtime_by_default(tmp_path):
    f = tmp_path / "song.flac"
    _write_flac(f)
    # Backdate the file so a bump would be obvious.
    old = os.stat(str(f)).st_mtime - 100_000
    os.utime(str(f), (old, old))
    assert write_bpm_tag(str(f), 128.0) is True
    assert os.stat(str(f)).st_mtime == old


def test_write_bpm_tag_bumps_mtime_when_disabled(tmp_path):
    f = tmp_path / "song.flac"
    _write_flac(f)
    old = os.stat(str(f)).st_mtime - 100_000
    os.utime(str(f), (old, old))
    assert write_bpm_tag(str(f), 128.0, preserve_mtime=False) is True
    assert os.stat(str(f)).st_mtime != old


def test_get_file_hash_is_size_colon_mtime(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"abc")
    st = os.stat(f)
    assert get_file_hash(str(f)) == f"{st.st_size}:{st.st_mtime}"


def test_get_file_hash_changes_with_content(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"abc")
    h1 = get_file_hash(str(f))
    f.write_bytes(b"abcdef")
    assert get_file_hash(str(f)) != h1
