"""Lyrics tag read/write (bpm.lyrics) and the LRCLIB client (integrations.lrclib)."""

import os

import numpy as np
import soundfile as sf
from mutagen.flac import FLAC
from mutagen.id3 import ID3

import bpm_tagger.integrations.lrclib as lrclib
from bpm_tagger.bpm.lyrics import is_synced, read_lyrics, sidecar_path, write_lyrics

LRC = "[00:12.00] First line\n[00:15.30] Second line"
PLAIN = "First line\nSecond line"


def _flac(path):
    sr = 22050
    y = (0.1 * np.sin(2 * np.pi * 220 * np.arange(sr) / sr)).astype("float32")
    sf.write(str(path), y, sr, format="FLAC")


# ── is_synced ─────────────────────────────────────────────────────────────────

def test_is_synced_detects_lrc_timestamps():
    assert is_synced(LRC) is True
    assert is_synced(PLAIN) is False
    assert is_synced("") is False
    assert is_synced(None) is False


# ── embedded read/write ───────────────────────────────────────────────────────

def test_flac_lyrics_roundtrip(tmp_path):
    f = tmp_path / "song.flac"
    _flac(f)
    assert write_lyrics(str(f), LRC) is True
    assert FLAC(str(f))["LYRICS"] == [LRC]
    assert read_lyrics(str(f)) == (LRC, "embedded")


def test_mp3_lyrics_uslt_roundtrip(tmp_path):
    # An empty .mp3 gains an ID3 header on write, like write_bpm_tag.
    f = tmp_path / "song.mp3"
    f.write_bytes(b"")
    assert write_lyrics(str(f), PLAIN) is True
    frames = ID3(str(f)).getall("USLT")
    assert frames and str(frames[0].text) == PLAIN
    assert read_lyrics(str(f)) == (PLAIN, "embedded")


def test_write_preserves_mtime(tmp_path):
    f = tmp_path / "song.flac"
    _flac(f)
    old = os.stat(str(f)).st_mtime - 100_000
    os.utime(str(f), (old, old))
    assert write_lyrics(str(f), PLAIN) is True
    assert os.stat(str(f)).st_mtime == old


# ── sidecar mode ──────────────────────────────────────────────────────────────

def test_sidecar_write_and_read(tmp_path):
    f = tmp_path / "song.flac"
    _flac(f)
    assert write_lyrics(str(f), LRC, mode="sidecar") is True
    sc = sidecar_path(str(f))
    assert os.path.isfile(sc)
    assert read_lyrics(str(f)) == (LRC, "sidecar")
    # The audio file's tags were not touched.
    assert "LYRICS" not in FLAC(str(f))


def test_remove_clears_tag_and_sidecar(tmp_path):
    f = tmp_path / "song.flac"
    _flac(f)
    write_lyrics(str(f), LRC)                      # embedded
    write_lyrics(str(f), PLAIN, mode="sidecar")    # + sidecar
    assert write_lyrics(str(f), "") is True
    assert read_lyrics(str(f)) is None
    assert not os.path.isfile(sidecar_path(str(f)))


# ── LRCLIB client ─────────────────────────────────────────────────────────────

class _FakeResp:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400 and self.status_code != 404:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


def test_lrclib_exact_get_hit(monkeypatch):
    def fake_get(url, params=None, **kw):
        assert url.endswith("/get")
        assert params["duration"] == 200
        return _FakeResp({"plainLyrics": PLAIN, "syncedLyrics": LRC, "instrumental": False})

    monkeypatch.setattr(lrclib.requests, "get", fake_get)
    r = lrclib.fetch_lyrics("ABBA", "SOS", album="ABBA", duration_ms=200_000)
    assert r == {"plain": PLAIN, "synced": LRC, "instrumental": False}


def test_lrclib_get_miss_falls_back_to_search_with_duration_filter(monkeypatch):
    def fake_get(url, params=None, **kw):
        if url.endswith("/get"):
            return _FakeResp({"code": 404}, status_code=404)
        return _FakeResp([
            # Wrong duration (a live version) — must be filtered out.
            {"duration": 260, "plainLyrics": "live version", "syncedLyrics": ""},
            {"duration": 201, "plainLyrics": PLAIN, "syncedLyrics": ""},
        ])

    monkeypatch.setattr(lrclib.requests, "get", fake_get)
    r = lrclib.fetch_lyrics("ABBA", "SOS", album="ABBA", duration_ms=200_000)
    assert r == {"plain": PLAIN, "synced": "", "instrumental": False}


def test_lrclib_search_prefers_synced(monkeypatch):
    def fake_get(url, params=None, **kw):
        return _FakeResp([
            {"duration": 200, "plainLyrics": "plain only", "syncedLyrics": ""},
            {"duration": 200, "plainLyrics": PLAIN, "syncedLyrics": LRC},
        ])

    monkeypatch.setattr(lrclib.requests, "get", fake_get)
    r = lrclib.fetch_lyrics("ABBA", "SOS", duration_ms=200_000)
    assert r["synced"] == LRC


def test_lrclib_instrumental(monkeypatch):
    monkeypatch.setattr(lrclib.requests, "get", lambda *a, **kw: _FakeResp(
        [{"duration": 200, "instrumental": True, "plainLyrics": "", "syncedLyrics": ""}]))
    r = lrclib.fetch_lyrics("Artist", "Interlude", duration_ms=200_000)
    assert r == {"plain": "", "synced": "", "instrumental": True}


def test_lrclib_failure_returns_none(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(lrclib.requests, "get", boom)
    assert lrclib.fetch_lyrics("A", "B") is None


def test_lrclib_blank_inputs_return_none():
    assert lrclib.fetch_lyrics("", "Title") is None
    assert lrclib.fetch_lyrics("Artist", " ") is None
