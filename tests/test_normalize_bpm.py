"""Characterization tests for _normalize_bpm — halve/double into [min, max]."""

import pytest

from bpm_tagger import _normalize_bpm


@pytest.mark.parametrize("bpm, lo, hi, expected", [
    (120.0, 60.0, 200.0, 120.0),   # already in range
    (200.0, 60.0, 200.0, 200.0),   # at upper bound
    (60.0, 60.0, 200.0, 60.0),     # at lower bound
    (40.0, 60.0, 200.0, 80.0),     # doubled once
    (30.0, 60.0, 200.0, 60.0),     # doubled to exactly min
    (400.0, 60.0, 200.0, 200.0),   # halved once to exactly max
    (410.0, 60.0, 200.0, 102.5),   # halved twice
    (15.0, 60.0, 200.0, 60.0),     # 15→30→60, stops at min
])
def test_normalize_in_range(bpm, lo, hi, expected):
    assert _normalize_bpm(bpm, lo, hi) == expected


@pytest.mark.parametrize("bpm, lo, hi", [
    (0.0, 60.0, 200.0),      # non-positive bpm short-circuits
    (-5.0, 60.0, 200.0),     # negative bpm short-circuits
    (120.0, 0.0, 200.0),     # non-positive min short-circuits
    (120.0, 200.0, 60.0),    # inverted bounds short-circuit
])
def test_normalize_short_circuit_returns_rounded_input(bpm, lo, hi):
    assert _normalize_bpm(bpm, lo, hi) == round(bpm, 1)


def test_normalize_rounds_to_one_decimal():
    assert _normalize_bpm(133.33, 60.0, 200.0) == 133.3
