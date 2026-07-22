"""Per-user accent hue: stored server-side so a chosen accent follows the
account across browsers/devices.

- Named player users store it on their ``players`` row.
- The admin stores it in settings.json.
- The shared Guest (RUN_PASSWORD) login has no account row → accepted as a
  no-op (the SPA keeps its per-browser value).
"""

import json
import os

import pytest

from bpm_tagger.db import BPMDatabase


# ── DB layer ────────────────────────────────────────────────────────────────
@pytest.fixture
def db(tmp_path):
    return BPMDatabase(str(tmp_path / "bpm.db"))


def test_set_player_accent_roundtrip_and_clamp(db):
    pid = db.add_player("runner", "hash", full_access=False)
    assert db.get_player(pid)["accent_hue"] is None      # default: no preference
    db.set_player_accent(pid, 185)
    assert db.get_player(pid)["accent_hue"] == 185
    db.set_player_accent(pid, 999)                        # clamped to 360
    assert db.get_player(pid)["accent_hue"] == 360
    db.set_player_accent(pid, None)                       # cleared
    assert db.get_player(pid)["accent_hue"] is None


# ── web API ─────────────────────────────────────────────────────────────────
def _app(base_config, **over):
    from bpm_tagger.config import build_config
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


def _login(client, **body):
    r = client.post("/api/login", json=body)
    csrf = None
    if r.status_code == 200:
        csrf = {"X-CSRF-Token": client.get("/api/me").get_json()["csrf_token"]}
    return r, csrf


def test_me_accent_null_by_default(client):
    _, csrf = _login(client, password="s3cret")
    assert client.get("/api/me").get_json()["accent_hue"] is None


def test_admin_accent_persists_to_settings_and_me(base_config):
    app = _app(base_config)
    client = app.test_client()
    _, csrf = _login(client, password="s3cret")

    r = client.post("/api/accent", json={"hue": 150}, headers=csrf)
    body = r.get_json()
    assert r.status_code == 200 and body["persisted"] is True and body["accent_hue"] == 150
    assert client.get("/api/me").get_json()["accent_hue"] == 150

    # Written through to settings.json.
    settings_path = os.path.join(os.path.dirname(base_config["db_path"]), "settings.json")
    with open(settings_path) as f:
        assert json.load(f)["accent_hue"] == 150

    # Clearing removes the key and reverts /api/me to null.
    assert client.post("/api/accent", json={"hue": None}, headers=csrf).status_code == 200
    assert client.get("/api/me").get_json()["accent_hue"] is None
    with open(settings_path) as f:
        assert "accent_hue" not in json.load(f)


def test_admin_accent_rejects_invalid_hue(base_config):
    app = _app(base_config)
    client = app.test_client()
    _, csrf = _login(client, password="s3cret")
    assert client.post("/api/accent", json={"hue": "purple"}, headers=csrf).status_code == 400


def test_player_accent_persists_on_account(base_config):
    app = _app(base_config)
    admin = app.test_client()
    _, acsrf = _login(admin, password="s3cret")
    admin.post("/api/players", json={"username": "runner", "password": "runrunrun"}, headers=acsrf)

    player = app.test_client()
    _, pcsrf = _login(player, username="runner", password="runrunrun")
    r = player.post("/api/accent", json={"hue": 55}, headers=pcsrf)
    assert r.status_code == 200 and r.get_json()["persisted"] is True
    assert player.get("/api/me").get_json()["accent_hue"] == 55

    # It's stored on the account, so a fresh login on another "device" sees it.
    other = app.test_client()
    _login(other, username="runner", password="runrunrun")
    assert other.get("/api/me").get_json()["accent_hue"] == 55


def test_guest_accent_is_noop(base_config):
    app = _app(base_config, run_password="runner99")
    guest = app.test_client()
    r, gcsrf = _login(guest, password="runner99")
    assert r.get_json()["role"] == "player"          # shared Guest → player role, no account
    resp = guest.post("/api/accent", json={"hue": 200}, headers=gcsrf)
    # Accepted, but not stored server-side.
    assert resp.status_code == 200 and resp.get_json()["persisted"] is False
    assert guest.get("/api/me").get_json()["accent_hue"] is None


def test_accent_requires_auth_and_csrf(base_config):
    app = _app(base_config)
    client = app.test_client()
    # Unauthenticated → 401.
    assert client.post("/api/accent", json={"hue": 100}).status_code == 401
    # Authenticated but missing CSRF header → 403.
    _login(client, password="s3cret")
    assert client.post("/api/accent", json={"hue": 100}).status_code == 403
