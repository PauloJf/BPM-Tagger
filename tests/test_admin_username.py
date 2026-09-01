"""Optional admin login username (UI_USERNAME / ui_username).

Password managers dislike a password-only form, so the admin account can be
named. The username is an identifier, not a second secret: the password still
authenticates, and a blank username keeps working so nobody can be locked out.
"""

import json

import pytest

from bpm_tagger.web.app import create_app


@pytest.fixture
def named_app(base_config, tmp_path):
    cfg = dict(base_config, ui_username="paulo")
    app = create_app(cfg)
    app.extensions["state"].settings_path = str(tmp_path / "settings.json")
    return app


def _csrf(c):
    return {"X-CSRF-Token": c.get("/api/me").get_json()["csrf_token"]}


def test_username_and_password_logs_in_admin(named_app):
    c = named_app.test_client()
    r = c.post("/api/login", json={"username": "paulo", "password": "s3cret"})
    assert r.status_code == 200
    assert r.get_json()["role"] == "admin"
    assert c.get("/api/me").get_json()["username"] == "paulo"


def test_username_match_is_case_insensitive(named_app):
    c = named_app.test_client()
    assert c.post("/api/login",
                  json={"username": " PauLo ", "password": "s3cret"}).status_code == 200


def test_blank_username_still_logs_in_admin(named_app):
    """Configuring a username must never lock the admin out."""
    c = named_app.test_client()
    r = c.post("/api/login", json={"password": "s3cret"})
    assert r.status_code == 200
    assert r.get_json()["role"] == "admin"


def test_wrong_username_is_rejected(named_app):
    c = named_app.test_client()
    r = c.post("/api/login", json={"username": "nobody", "password": "s3cret"})
    assert r.status_code == 401
    assert r.get_json()["error"] == "invalid_password"


def test_unconfigured_username_keeps_password_only_login(app):
    """Default install: no admin username, so any username is a player attempt."""
    c = app.test_client()
    assert c.post("/api/login", json={"password": "s3cret"}).status_code == 200
    c2 = app.test_client()
    assert c2.post("/api/login",
                   json={"username": "admin", "password": "s3cret"}).status_code == 401


def test_guest_password_still_needs_a_blank_username(base_config, tmp_path):
    cfg = dict(base_config, ui_username="paulo", run_password="guestguest")
    app = create_app(cfg)
    app.extensions["state"].settings_path = str(tmp_path / "settings.json")
    c = app.test_client()
    assert c.post("/api/login", json={"password": "guestguest"}).get_json()["role"] == "player"
    c2 = app.test_client()
    assert c2.post("/api/login",
                   json={"username": "paulo", "password": "guestguest"}).status_code == 401


def test_setting_username_from_settings_applies_without_restart(app, tmp_path):
    settings = tmp_path / "settings.json"
    app.extensions["state"].settings_path = str(settings)
    c = app.test_client()
    assert c.post("/api/login", json={"password": "s3cret"}).status_code == 200
    r = c.post("/api/settings/username", json={"ui_username": "Paulo"}, headers=_csrf(c))
    assert r.status_code == 200 and r.get_json()["ui_username"] == "Paulo"
    assert json.loads(settings.read_text())["ui_username"] == "Paulo"
    c2 = app.test_client()
    assert c2.post("/api/login",
                   json={"username": "paulo", "password": "s3cret"}).status_code == 200
    # Clearing it drops the key and restores the password-only login.
    assert c.post("/api/settings/username", json={"ui_username": ""},
                  headers=_csrf(c)).status_code == 200
    assert "ui_username" not in json.loads(settings.read_text())
    c3 = app.test_client()
    assert c3.post("/api/login",
                   json={"username": "paulo", "password": "s3cret"}).status_code == 401


def test_username_rejects_bad_characters(app, tmp_path):
    app.extensions["state"].settings_path = str(tmp_path / "settings.json")
    c = app.test_client()
    c.post("/api/login", json={"password": "s3cret"})
    r = c.post("/api/settings/username", json={"ui_username": "bad name!"}, headers=_csrf(c))
    assert r.status_code == 400


def test_admin_and_player_usernames_cannot_collide(app, tmp_path):
    app.extensions["state"].settings_path = str(tmp_path / "settings.json")
    c = app.test_client()
    c.post("/api/login", json={"password": "s3cret"})
    csrf = _csrf(c)
    assert c.post("/api/players", json={"username": "runner", "password": "runrunrun"},
                  headers=csrf).status_code == 200
    # Admin username can't take a player's name...
    assert c.post("/api/settings/username", json={"ui_username": "Runner"},
                  headers=csrf).status_code == 409
    # ...nor the other way round.
    assert c.post("/api/settings/username", json={"ui_username": "paulo"},
                  headers=csrf).status_code == 200
    assert c.post("/api/players", json={"username": "Paulo", "password": "runrunrun"},
                  headers=csrf).status_code == 409
