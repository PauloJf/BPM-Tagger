"""load_audio: the ffmpeg fallback for formats libsndfile can't decode.

librosa dropped its audioread fallback in 1.0, and `librosa>=0.10.0` was
unpinned, so every .m4a/.aac in a library started failing with "Format not
recognised" — libsndfile has no AAC support. load_audio restores a fallback we
control, shelling out to ffmpeg.

The decode tests need a real ffmpeg (present on CI runners, often not on a dev
box) and skip without it; the routing and shape logic is covered either way.
"""

import shutil
import subprocess

import numpy as np
import pytest
import soundfile as sf

from bpm_tagger.bpm.audio import AudioLoadError, load_audio

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")

SR = 22050


def _tone(seconds=2.0, freq=220.0, sr=SR):
    t = np.arange(int(seconds * sr)) / sr
    return (0.3 * np.sin(2 * np.pi * freq * t)).astype("float32")


@pytest.fixture
def wav(tmp_path):
    p = tmp_path / "tone.wav"
    sf.write(str(p), _tone(), SR)
    return str(p)


@pytest.fixture
def aac(tmp_path):
    """A real .m4a — the format that regressed."""
    src = tmp_path / "src.wav"
    sf.write(str(src), _tone(), SR)
    out = tmp_path / "tone.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-c:a", "aac", str(out)],
                   check=True, capture_output=True, timeout=60)
    return str(out)


# ── the path librosa still handles ───────────────────────────────────────────

def test_wav_loads_and_keeps_librosas_shape(wav):
    y, sr = load_audio(wav, sr=None, mono=True)
    assert sr == SR
    assert y.ndim == 1 and y.size > 0


def test_resamples_when_asked(wav):
    y, sr = load_audio(wav, sr=2000, mono=True)
    assert sr == 2000
    assert 3900 < y.size < 4100          # ~2 s at 2 kHz


def test_offset_and_duration_window(wav):
    y, sr = load_audio(wav, sr=SR, mono=True, offset=0.5, duration=1.0)
    assert abs(y.size - SR) < SR * 0.05  # ~1 s

# ── the regression: AAC, which libsndfile cannot decode ──────────────────────


@needs_ffmpeg
def test_libsndfile_really_cannot_read_aac(aac):
    """Guards the premise: if this ever starts passing, the fallback is moot."""
    with pytest.raises(Exception):
        sf.read(aac)


@needs_ffmpeg
def test_aac_loads_via_the_ffmpeg_fallback(aac):
    y, sr = load_audio(aac, sr=None, mono=True)
    assert y.ndim == 1
    assert y.size > SR                   # ~2 s of audio came back
    assert sr > 0
    assert np.abs(y).max() > 0.01        # actual signal, not silence


@needs_ffmpeg
def test_aac_respects_target_sample_rate(aac):
    y, sr = load_audio(aac, sr=2000, mono=True)
    assert sr == 2000
    assert 3500 < y.size < 4500


@needs_ffmpeg
def test_aac_window_is_honoured(aac):
    whole, _ = load_audio(aac, sr=SR, mono=True)
    part, _ = load_audio(aac, sr=SR, mono=True, offset=0.5, duration=0.5)
    assert part.size < whole.size
    assert abs(part.size - SR * 0.5) < SR * 0.15


@needs_ffmpeg
def test_stereo_comes_back_channels_first(tmp_path):
    """librosa's mono=False contract is (channels, samples) — ffmpeg interleaves."""
    src = tmp_path / "st.wav"
    mono = _tone()
    sf.write(str(src), np.stack([mono, mono * 0.5], axis=1), SR)
    out = tmp_path / "st.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-c:a", "aac", str(out)],
                   check=True, capture_output=True, timeout=60)
    y, _ = load_audio(str(out), sr=SR, mono=False)
    assert y.ndim == 2 and y.shape[0] == 2
    assert y.shape[1] > SR


# ── failure handling ─────────────────────────────────────────────────────────

def test_undecodable_file_raises_audioloaderror(tmp_path):
    junk = tmp_path / "not-audio.m4a"
    junk.write_bytes(b"this is not audio at all")
    with pytest.raises(AudioLoadError):
        load_audio(str(junk))


def test_missing_file_raises_rather_than_hanging(tmp_path):
    with pytest.raises(Exception):
        load_audio(str(tmp_path / "nope.m4a"))


def test_reports_clearly_when_ffmpeg_is_absent(tmp_path, monkeypatch):
    junk = tmp_path / "x.m4a"
    junk.write_bytes(b"nope")
    monkeypatch.setattr("bpm_tagger.bpm.audio.shutil.which", lambda name: None)
    with pytest.raises(AudioLoadError):
        load_audio(str(junk))
