"""Waveform back-fill API + the COMPUTE_WAVEFORMS scan setting."""

import os
import sqlite3
import time

import pytest

from bpm_tagger.config import build_config

PEAKS = '{"peaks": [0.25]}'


def _app(base_config, **over):
    from bpm_tagger.web.app import create_app

    cfg = build_config()
    cfg.update({
        "db_path": base_config["db_path"],
        "music_dir": base_config["music_dir"],
        "ui_password": "s3cret",
        "ui_secret_key": "unit-test-secret-key",
        "write_tags": False,
    })
    cfg.update(over)
    os.makedirs(cfg["music_dir"], exist_ok=True)
    app = create_app(cfg)
    app.config["TESTING"] = True
    return app


def _login(client, password="s3cret"):
    client.post("/api/login", json={"password": password})
    return {"X-CSRF-Token": client.get("/api/me").get_json()["csrf_token"]}


def _insert(db_path, path, status="done", peaks=None):
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO tracks (file_path, status, waveform_peaks, analyzed_at) "
                 "VALUES (?, ?, ?, datetime('now'))", (path, status, peaks))
    conn.commit()
    conn.close()


def _wait_idle(client, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get("/api/waveform/fill/status").get_json()
        if not body["running"]:
            return body
        time.sleep(0.05)
    pytest.fail("waveform fill never finished")


def test_status_counts_only_analyzed_tracks_without_peaks(base_config):
    app = _app(base_config)
    client = app.test_client()
    _login(client)
    music = base_config["music_dir"]
    _insert(base_config["db_path"], f"{music}/missing.flac")
    _insert(base_config["db_path"], f"{music}/has.flac", peaks=PEAKS)
    _insert(base_config["db_path"], f"{music}/broken.flac", status="error")
    body = client.get("/api/waveform/fill/status").get_json()
    assert body["running"] is False and body["remaining"] == 1


def test_fill_computes_missing_and_skips_filled(base_config, monkeypatch):
    import bpm_tagger.web.api.waveform as wf

    seen = []
    monkeypatch.setattr(wf, "compute_waveform_peaks",
                        lambda path: seen.append(path) or PEAKS)
    app = _app(base_config)
    client = app.test_client()
    csrf = _login(client)
    music = base_config["music_dir"]
    _insert(base_config["db_path"], f"{music}/a.flac")
    _insert(base_config["db_path"], f"{music}/b.flac", peaks='{"peaks": [1]}')

    assert client.post("/api/waveform/fill/start", json={}, headers=csrf).get_json()["ok"]
    body = _wait_idle(client)
    assert seen == [f"{music}/a.flac"]
    assert body["filled"] == 1 and body["failed"] == 0 and body["remaining"] == 0


def test_fill_counts_unreadable_files_as_failed(base_config, monkeypatch):
    import bpm_tagger.web.api.waveform as wf

    monkeypatch.setattr(wf, "compute_waveform_peaks", lambda path: None)
    app = _app(base_config)
    client = app.test_client()
    csrf = _login(client)
    _insert(base_config["db_path"], f"{base_config['music_dir']}/bad.flac")
    client.post("/api/waveform/fill/start", json={}, headers=csrf)
    body = _wait_idle(client)
    assert body["failed"] == 1 and body["remaining"] == 1


def test_fill_routes_are_closed_to_the_player_role(base_config):
    client = _app(base_config, run_password="runpw").test_client()
    csrf = _login(client, "runpw")
    assert client.post("/api/waveform/fill/start", json={}, headers=csrf).status_code == 403


@pytest.mark.parametrize("sent,stored", [("never", "auto"), ("TRUE", "true"), ("false", "false")])
def test_scan_settings_accept_the_waveform_mode(base_config, sent, stored):
    app = _app(base_config)
    client = app.test_client()
    csrf = _login(client)
    r = client.post("/api/settings/scan", json={"compute_waveforms": sent}, headers=csrf)
    assert r.status_code == 200
    assert client.get("/api/settings").get_json()["settings"]["compute_waveforms"] == stored
