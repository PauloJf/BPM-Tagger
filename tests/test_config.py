"""Characterization tests for settings.json override loading."""

import json

from bpm_tagger import load_settings_override, settings_file_path


def test_settings_file_path_is_sibling_of_db(tmp_path):
    db = tmp_path / "sub" / "bpm_tagger.db"
    assert settings_file_path(str(db)) == str(tmp_path / "sub" / "settings.json")


def test_override_missing_file_is_noop(tmp_path):
    cfg = {"db_path": str(tmp_path / "bpm_tagger.db"), "mode": "watch", "workers": 1}
    out = load_settings_override(cfg)
    assert out is cfg
    assert out["mode"] == "watch"
    assert out["workers"] == 1


def test_override_overwrites_env_values(tmp_path):
    db = tmp_path / "bpm_tagger.db"
    (tmp_path / "settings.json").write_text(
        json.dumps({"mode": "scan_all", "workers": 4, "new_key": "x"})
    )
    cfg = {"db_path": str(db), "mode": "watch", "workers": 1}
    out = load_settings_override(cfg)
    assert out["mode"] == "scan_all"
    assert out["workers"] == 4
    assert out["new_key"] == "x"


def test_override_drops_dead_settings(tmp_path):
    """Settings removed in a past release don't leak back in from a stale file.

    v2.10.0 folded match tolerance + force tempo into the max-stretch limit; an
    upgraded install still has both keys sitting in its settings.json."""
    db = tmp_path / "bpm_tagger.db"
    (tmp_path / "settings.json").write_text(json.dumps({
        "run_tolerance_pct": 2.0, "run_force_tempo": True, "run_stretch_limit_pct": 20,
    }))
    out = load_settings_override({"db_path": str(db)})
    assert "run_tolerance_pct" not in out
    assert "run_force_tempo" not in out
    assert out["run_stretch_limit_pct"] == 20      # the surviving key still applies


def test_override_corrupt_json_is_ignored(tmp_path):
    db = tmp_path / "bpm_tagger.db"
    (tmp_path / "settings.json").write_text("{ not valid json ")
    cfg = {"db_path": str(db), "mode": "watch"}
    out = load_settings_override(cfg)
    assert out["mode"] == "watch"
