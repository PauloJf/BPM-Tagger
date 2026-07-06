"""Characterization tests for _reconcile — combining detector BPM values."""

import pytest

from bpm_tagger import _reconcile


def cfg(**over):
    base = {
        "review_disagree_threshold": 15.0,
        "bpm_min": 60.0,
        "bpm_max": 200.0,
        "octave_correction": True,
    }
    base.update(over)
    return base


def test_both_neural_agree_averages_no_review():
    bpm, review = _reconcile(120.0, 125.0, 118.0, cfg())
    assert bpm == 122.5
    assert review is False


def test_both_neural_octave_picks_in_range_value():
    # 120 vs 60 is a 2x octave; the in-range value (120) is chosen, no review.
    bpm, review = _reconcile(120.0, 60.0, 0.0, cfg())
    assert bpm == 120.0
    assert review is False


def test_both_neural_octave_correction_disabled_flags_review():
    # With octave correction off, 120 vs 60 are "far apart" → tiebreak + review.
    bpm, review = _reconcile(120.0, 60.0, 122.0, cfg(octave_correction=False))
    assert review is True


def test_both_neural_disagree_uses_librosa_tiebreak():
    # dr=120 es=160 disagree; lb=155 is closer to es → choose es, flag review.
    bpm, review = _reconcile(120.0, 160.0, 155.0, cfg())
    assert bpm == 160.0
    assert review is True


def test_only_deeprhythm_no_librosa_flags_review():
    bpm, review = _reconcile(128.0, None, 0.0, cfg())
    assert bpm == 128.0
    assert review is True


def test_only_deeprhythm_close_to_librosa_no_review():
    bpm, review = _reconcile(128.0, None, 130.0, cfg())
    assert bpm == 128.0
    assert review is False


def test_only_deeprhythm_far_from_librosa_flags_review():
    bpm, review = _reconcile(128.0, None, 100.0, cfg())
    assert bpm == 128.0
    assert review is True


def test_only_essentia_close_to_librosa_no_review():
    bpm, review = _reconcile(None, 90.0, 92.0, cfg())
    assert bpm == 90.0
    assert review is False


def test_librosa_only_always_review():
    bpm, review = _reconcile(None, None, 140.0, cfg())
    assert bpm == 140.0
    assert review is True
