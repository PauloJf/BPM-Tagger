"""Characterization tests for write_bpm_tag and get_file_hash."""

import os

import numpy as np
import soundfile as sf
from mutagen.flac import FLAC
from mutagen.id3 import ID3

from bpm_tagger import get_file_hash, write_bpm_tag


def _write_flac(path):
    sr = 22050
    y = (0.1 * np.sin(2 * np.pi * 220 * np.arange(sr) / sr)).astype("float32")
    sf.write(str(path), y, sr, format="FLAC")


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


def test_write_bpm_tag_unsupported_returns_false(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("not audio")
    assert write_bpm_tag(str(f), 120.0) is False


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
