"""COMPUTE_WAVEFORMS: scans compute waveform peaks only when something will show
them (docs/plans/core-decoupling.md, step A)."""

import numpy as np
import pytest
import soundfile as sf

import bpm_tagger.scan.scanner as scanner_mod
from bpm_tagger.config import _parse_waveform_mode, build_config, waveforms_enabled
from bpm_tagger.scan.scanner import BPMTagger

pytestmark = pytest.mark.core  # runs in the core-only CI job

FAKE_PEAKS = '{"peaks": [0.5]}'


@pytest.mark.parametrize("raw,expected", [
    ("", "auto"), ("auto", "auto"), ("TRUE", "true"), ("on", "true"),
    ("false", "false"), ("0", "false"), ("bogus", "auto"),
])
def test_env_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("COMPUTE_WAVEFORMS", raw)
    assert _parse_waveform_mode() == expected


@pytest.mark.parametrize("mode,ui,expected", [
    ("auto", True, True), ("auto", False, False),
    ("true", False, True), ("false", True, False),
])
def test_waveforms_enabled(mode, ui, expected):
    assert waveforms_enabled({"compute_waveforms": mode, "enable_ui": ui}) is expected


def _tagger(tmp_path, monkeypatch, **over):
    monkeypatch.setenv("MUSIC_DIR", str(tmp_path / "music"))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "bpm.db"))
    cfg = build_config()
    cfg.update(write_tags=False, measure_loudness=False, **over)
    # Detection itself is not under test here — keep it instant and deterministic.
    monkeypatch.setattr(scanner_mod, "detect_bpm", lambda path, config, progress: {
        "bpm": 120.0, "bpm_dr": None, "bpm_es": None, "bpm_lb": 120.0,
        "confidence": 0.9, "detector": "librosa", "needs_review": False})
    return BPMTagger(cfg)


def _flac(tmp_path):
    music = tmp_path / "music"
    music.mkdir(exist_ok=True)
    f = music / "song.flac"
    sf.write(str(f), np.zeros(22050, dtype="float32"), 22050, format="FLAC")
    return str(f)


def test_headless_scan_skips_waveforms(tmp_path, monkeypatch):
    t = _tagger(tmp_path, monkeypatch, enable_ui=False, compute_waveforms="auto")

    def boom(path):
        raise AssertionError("waveform computed on a headless scan")
    monkeypatch.setattr(scanner_mod, "compute_waveform_peaks", boom)

    f = _flac(tmp_path)
    assert t.process_file(f)["status"] == "tagged"
    row = t.db.get_track(f)
    assert row["status"] == "done" and row["waveform_peaks"] is None
    assert t.db.count_missing_waveforms() == 1
    assert t.db.get_missing_waveform_paths(limit=10) == [f]


@pytest.mark.parametrize("over", [
    {"enable_ui": True, "compute_waveforms": "auto"},
    {"enable_ui": False, "compute_waveforms": "true"},
])
def test_ui_or_forced_scan_stores_waveforms(tmp_path, monkeypatch, over):
    t = _tagger(tmp_path, monkeypatch, **over)
    monkeypatch.setattr(scanner_mod, "compute_waveform_peaks", lambda path: FAKE_PEAKS)
    f = _flac(tmp_path)
    t.process_file(f)
    assert t.db.get_track(f)["waveform_peaks"] == FAKE_PEAKS
    assert t.db.count_missing_waveforms() == 0


def test_skipping_never_wipes_stored_peaks(tmp_path, monkeypatch):
    t = _tagger(tmp_path, monkeypatch, enable_ui=True, compute_waveforms="auto")
    monkeypatch.setattr(scanner_mod, "compute_waveform_peaks", lambda path: FAKE_PEAKS)
    f = _flac(tmp_path)
    t.process_file(f)
    t.config["compute_waveforms"] = "false"
    t.process_file(f, force=True)
    assert t.db.get_track(f)["waveform_peaks"] == FAKE_PEAKS
