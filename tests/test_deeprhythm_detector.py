"""The DeepRhythm detector's decoding path.

DeepRhythm's own predict() calls librosa.load internally, so when librosa 1.0
dropped the audioread fallback it stopped reading AAC — and reported it as
"'NoneType' object has no attribute 'to'", because the failed load returns None
straight into a tensor call. Every .m4a in a library produced that warning and
the CNN never contributed, silently reducing the :full image to two detectors.

The fix decodes with bpm.audio.load_audio and feeds samples to
predict_from_audio(). deeprhythm itself is absent from the slim image and from
CI, so the predictor is stubbed; what is under test is our call shape.
"""

import sys
import types

import numpy as np
import pytest

import bpm_tagger.bpm.detectors as det

pytestmark = pytest.mark.core  # runs in the core-only CI job

SR = det._DR_SR


class _StubPredictor:
    """Stands in for DeepRhythmPredictor, recording how it was called."""

    def __init__(self):
        self.audio = None
        self.sr = None
        self.file_calls = []

    def predict_from_audio(self, audio, sr, include_confidence=False):
        self.audio, self.sr = audio, sr
        return 128.4

    def predict(self, filename, include_confidence=False):
        # The old path. Nothing should reach it: it re-loads with librosa and
        # reintroduces the very format gap this detector works around.
        self.file_calls.append(filename)
        raise AssertionError("predict(filename) must not be used")


@pytest.fixture
def stub(monkeypatch):
    p = _StubPredictor()
    monkeypatch.setattr(det, "_get_predictor", lambda: p)
    return p


@pytest.fixture
def audio(monkeypatch):
    """Ten seconds of samples — comfortably over the 8-second clip minimum."""
    y = np.zeros(SR * 10, dtype="float32")
    monkeypatch.setattr(det, "load_audio", lambda path, **kw: (y, SR))
    return y


def test_feeds_decoded_samples_not_the_filename(stub, audio):
    assert det._detect_bpm_deeprhythm("/music/x.m4a") == 128.4
    assert stub.audio is audio
    assert stub.sr == SR
    assert stub.file_calls == []


def test_requests_the_rate_deeprhythm_expects(stub, monkeypatch):
    seen = {}

    def fake_load(path, **kw):
        seen.update(kw)
        return np.zeros(SR * 10, dtype="float32"), SR

    monkeypatch.setattr(det, "load_audio", fake_load)
    det._detect_bpm_deeprhythm("/music/x.m4a")
    assert seen["sr"] == SR and seen["mono"] is True


def test_audio_shorter_than_one_clip_returns_none(stub, monkeypatch):
    # split_audio() yields no clips below 8 s and returns None, which used to
    # surface as the same opaque 'NoneType' .to error.
    monkeypatch.setattr(det, "load_audio",
                        lambda path, **kw: (np.zeros(SR * 3, dtype="float32"), SR))
    assert det._detect_bpm_deeprhythm("/music/short.m4a") is None
    assert stub.audio is None


def test_decode_failure_propagates_for_the_caller_to_log(monkeypatch, stub):
    from bpm_tagger.bpm.audio import AudioLoadError

    def boom(path, **kw):
        raise AudioLoadError("ffmpeg could not decode it")

    monkeypatch.setattr(det, "load_audio", boom)
    # pipeline.detect_bpm wraps this in try/except and logs a warning; the point
    # is that the message now names the real problem.
    with pytest.raises(AudioLoadError, match="decode"):
        det._detect_bpm_deeprhythm("/music/broken.m4a")


def test_a_falsy_prediction_becomes_none(monkeypatch, audio):
    class Zero:
        def predict_from_audio(self, a, sr, include_confidence=False):
            return 0

    monkeypatch.setattr(det, "_get_predictor", lambda: Zero())
    assert det._detect_bpm_deeprhythm("/music/x.m4a") is None


def test_predictor_is_created_once_per_thread(monkeypatch):
    """_get_predictor caches on a threading.local; loading the CNN is expensive."""
    built = []

    class FakeModule(types.ModuleType):
        def __init__(self):
            super().__init__("deeprhythm")

        def DeepRhythmPredictor(self, *a, **kw):  # noqa: N802 — mirrors the real name
            built.append(1)
            return _StubPredictor()

    monkeypatch.setitem(sys.modules, "deeprhythm", FakeModule())
    monkeypatch.setattr(det, "_local", type("L", (), {})())
    det._get_predictor()
    det._get_predictor()
    assert len(built) == 1
