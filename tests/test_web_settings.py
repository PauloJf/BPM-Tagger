"""Settings-save endpoints: persistence to settings.json + live AppState update.

Guards the M0 AppState conversion (risk #1) and the settings-POST side effects.
"""

import json

import pytest


@pytest.fixture
def auth_client(client):
    with client.session_transaction() as sess:
        sess["csrf_token"] = "tok"
    client.post("/login", data={"csrf_token": "tok", "password": "s3cret"})
    return client


def _state(client):
    return client.application.extensions["state"]


def test_settings_scan_persists_and_updates_state(auth_client):
    resp = auth_client.post("/settings/scan", data={
        "csrf_token": "tok",
        "workers": "2",
        "write_tags": "on",
        "review_confidence_threshold": "0.6",
        "bpm_min": "70",
        "bpm_max": "180",
    })
    assert resp.status_code == 302

    st = _state(auth_client)
    # Live state reflects the change immediately (no restart).
    assert st.conf_threshold == 0.6
    assert st.bpm_min == 70.0
    assert st.bpm_max == 180.0
    assert st.write_tags is True
    assert st.config["workers"] == 2

    # And it is persisted to settings.json.
    with open(st.settings_path) as f:
        saved = json.load(f)
    assert saved["workers"] == 2
    assert saved["bpm_min"] == 70.0
    assert saved["review_confidence_threshold"] == 0.6


def test_settings_scan_requires_csrf(auth_client):
    resp = auth_client.post("/settings/scan", data={"workers": "3"})
    assert resp.status_code == 403


def test_settings_mode_rejects_invalid_mode(auth_client):
    resp = auth_client.post("/settings/mode",
                            data={"csrf_token": "tok", "mode": "bogus"},
                            follow_redirects=False)
    assert resp.status_code == 302
    st = _state(auth_client)
    assert st.config.get("mode") != "bogus"


def test_settings_playback_clamps_and_persists(auth_client):
    auth_client.post("/settings/playback",
                     data={"csrf_token": "tok", "playback_buffer": "99"})
    st = _state(auth_client)
    assert st.config["playback_buffer"] == 30.0  # clamped to max
