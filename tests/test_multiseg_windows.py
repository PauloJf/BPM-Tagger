"""Multi-segment librosa analysis when a window decodes to nothing.

From the v2.17.3 scan logs, on an .m4a with damaged trailing frames:

    librosa/core/spectrum.py:3004: UserWarning: n_fft=2048 is too large for
    input signal of length=0

A container can declare more audio than actually decodes, so an offset derived
from that duration lands past the real end and the window comes back empty. It
was handed to librosa regardless, which warned and produced a 0 BPM that then
dragged the median of the surviving windows down.
"""

import numpy as np
import pytest

import bpm_tagger.bpm.detectors as det

pytestmark = pytest.mark.core  # runs in the core-only CI job


@pytest.fixture
def no_real_audio(monkeypatch):
    """Report a long duration so several segment offsets get computed."""
    monkeypatch.setattr(det, "_track_duration", lambda p: 300.0)


def _windows(monkeypatch, results):
    """Feed _detect_bpm_librosa_multiseg a fixed sequence of window results."""
    it = iter(results)
    monkeypatch.setattr(det, "_librosa_window", lambda p, o, d: next(it))


def test_empty_windows_are_dropped_from_the_median(no_real_audio, monkeypatch):
    # Two good windows and one that decoded to nothing. The median of
    # [128, 0, 130] would be 128 — but of the usable pair it is 129.
    _windows(monkeypatch, [(128.0, 0.9), (0.0, 0.0), (130.0, 0.9)])
    bpm, conf, total, empty = det._detect_bpm_librosa_multiseg("/music/x.m4a", 3, 45.0)
    assert bpm == 129.0
    assert conf == pytest.approx(0.9)
    assert (total, empty) == (3, 1)


def test_a_single_empty_window_does_not_halve_the_result(no_real_audio, monkeypatch):
    _windows(monkeypatch, [(120.0, 0.8), (120.0, 0.8), (0.0, 0.0)])
    bpm, _, total, empty = det._detect_bpm_librosa_multiseg("/music/x.m4a", 3, 45.0)
    assert bpm == 120.0
    assert (total, empty) == (3, 1)


def test_all_windows_empty_reports_nothing_rather_than_zero_bpm(no_real_audio, monkeypatch):
    _windows(monkeypatch, [(0.0, 0.0)] * 3)
    assert det._detect_bpm_librosa_multiseg("/music/x.m4a", 3, 45.0) == (0.0, 0.0, 3, 3)


def test_window_guard_skips_librosa_entirely_on_empty_audio(monkeypatch):
    """_librosa_window must not pass a zero-length array to librosa at all."""
    monkeypatch.setattr(det, "load_audio", lambda p, **kw: (np.array([], "float32"), 22050))

    def explode(*a, **kw):
        raise AssertionError("librosa was called with empty audio")

    monkeypatch.setattr(det.librosa.onset, "onset_strength", explode)
    assert det._librosa_window("/music/x.m4a", 280.0, 45.0) == (0.0, 0.0)
