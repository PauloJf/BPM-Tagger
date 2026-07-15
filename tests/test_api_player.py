"""Player-only ("Run-only") role: login, default-deny scope, settings filtering.

A second password grants a restricted "player" session that may reach ONLY the
Run page's endpoints; everything else is 403'd by the app-factory gate. Admin
login is unchanged, and the feature is dormant until a run password is set.
"""

import os
import re
import sqlite3
import time
from email.utils import parsedate_to_datetime


def _app(base_config, **over):
    """A test app built from the full default config (so run_* / navidrome_url
    keys exist), with test overrides and an optional run password applied."""
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


def _client(app):
    return app.test_client()


def _login(client, password):
    resp = client.post("/api/login", json={"password": password})
    csrf = None
    if resp.status_code == 200:
        csrf = {"X-CSRF-Token": client.get("/api/me").get_json()["csrf_token"]}
    return resp, csrf


def _session_ttl(resp):
    """Approx seconds until the session cookie expires, or None for a
    browser-session cookie (no Expires/Max-Age — dies when the browser closes).
    Flask sets the session cookie via ``expires=`` (an Expires date), so read
    that; fall back to Max-Age if present."""
    for c in resp.headers.getlist("Set-Cookie"):
        if not c.startswith("session="):
            continue
        m = re.search(r"Max-Age=(\d+)", c)
        if m:
            return int(m.group(1))
        m = re.search(r"Expires=([^;]+)", c)
        if m:
            try:
                return parsedate_to_datetime(m.group(1)).timestamp() - time.time()
            except Exception:
                return None
    return None


def _seed(db_path, music_dir, name="song", bpm=150.0):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO tracks (file_path, title, artist, bpm, status) "
        "VALUES (?, ?, 'Artist', ?, 'done')",
        (f"{music_dir}/{name}.mp3", name, bpm))
    conn.commit()
    conn.close()
    return f"{music_dir}/{name}.mp3"


# ── login / role ───────────────────────────────────────────────────────────────

def test_run_password_grants_player_role(base_config):
    client = _client(_app(base_config, run_password="runner99"))
    resp, _ = _login(client, "runner99")
    assert resp.status_code == 200 and resp.get_json()["role"] == "player"
    assert client.get("/api/me").get_json()["role"] == "player"


def test_admin_password_still_admin(base_config):
    client = _client(_app(base_config, run_password="runner99"))
    resp, _ = _login(client, "s3cret")
    assert resp.status_code == 200 and resp.get_json()["role"] == "admin"


def test_no_run_password_means_no_player(base_config):
    # Feature dormant: the run password is unset, so it can't be used to log in.
    client = _client(_app(base_config))  # no run_password
    resp, _ = _login(client, "runner99")
    assert resp.status_code == 401


# ── default-deny scope ───────────────────────────────────────────────────────────

def test_player_blocked_from_admin_endpoints(base_config):
    client = _client(_app(base_config, run_password="runner99"))
    _login(client, "runner99")
    for path in ("/api/tracks", "/api/stats", "/api/duplicates"):
        assert client.get(path).status_code == 403, path


def test_player_cannot_save_bpm_or_change_settings(base_config):
    app = _app(base_config, run_password="runner99")
    client = _client(app)
    _, csrf = _login(client, "runner99")
    path = _seed(base_config["db_path"], base_config["music_dir"])
    assert client.post("/api/save_bpm", json={"file_path": path, "bpm": 120},
                       headers=csrf).status_code == 403
    assert client.post("/api/settings/password",
                       json={"current_password": "runner99", "new_password": "x" * 8,
                             "confirm_password": "x" * 8}, headers=csrf).status_code == 403


def test_player_may_play_star_dislike_scrobble(base_config):
    client = _client(_app(base_config, run_password="runner99"))
    _, csrf = _login(client, "runner99")
    path = _seed(base_config["db_path"], base_config["music_dir"])
    # Build queue (GET, no CSRF) is allowed and returns the seeded track.
    q = client.get("/api/run/queue?bpm=150")
    assert q.status_code == 200 and q.get_json()["tracks"]
    # The three approved mutations pass the scope gate (not 403).
    assert client.post("/api/track/star", json={"path": path, "starred": True},
                       headers=csrf).status_code == 200
    assert client.post("/api/track/dislike", json={"path": path, "disliked": True},
                       headers=csrf).status_code == 200
    assert client.post("/api/scrobble", json={"path": path},
                       headers=csrf).status_code != 403


def test_player_settings_is_filtered_to_run_keys(base_config):
    client = _client(_app(base_config, run_password="runner99"))
    _login(client, "runner99")
    settings = client.get("/api/settings").get_json()["settings"]
    assert "run_octave_fold" in settings          # run keys present
    assert "navidrome_url" not in settings         # full config not leaked
    assert "ui_password" not in settings
    assert "run_password" not in settings
    assert "run_password_hash" not in settings


# ── admin management of the run password ─────────────────────────────────────────

def test_admin_sets_and_disables_run_password(base_config):
    app = _app(base_config)  # starts with no run password
    client = _client(app)
    _, csrf = _login(client, "s3cret")

    # Set it → a second client can then log in as a player.
    r = client.post("/api/settings/run-password",
                    json={"new_password": "runner99", "confirm_password": "runner99"},
                    headers=csrf)
    assert r.status_code == 200 and r.get_json()["enabled"] is True
    p = _client(app)
    resp, _ = _login(p, "runner99")
    assert resp.status_code == 200 and resp.get_json()["role"] == "player"

    # Disable it → the run password no longer logs anyone in.
    assert client.post("/api/settings/run-password", json={"disable": True},
                       headers=csrf).status_code == 200
    resp2, _ = _login(_client(app), "runner99")
    assert resp2.status_code == 401


# ── session length ───────────────────────────────────────────────────────────

def test_player_session_is_longer_than_admin(base_config):
    app = _app(base_config, run_password="runner99", run_session_days=30,
               ui_session_hours=24)
    pttl = _session_ttl(_login(_client(app), "runner99")[0])
    attl = _session_ttl(_login(_client(app), "s3cret")[0])
    # Player ~30 days; admin honors UI_SESSION_HOURS (~24h); both persistent.
    assert pttl is not None and pttl >= 29 * 86400
    assert attl is not None and 23 * 3600 <= attl <= 25 * 3600
    assert pttl > attl


def test_admin_changes_player_session_length(base_config):
    app = _app(base_config, run_password="runner99")
    admin = _client(app)
    _, csrf = _login(admin, "s3cret")
    r = admin.post("/api/settings/run-session", json={"run_session_days": 7}, headers=csrf)
    assert r.status_code == 200 and r.get_json()["run_session_days"] == 7
    presp, _ = _login(_client(app), "runner99")
    ttl = _session_ttl(presp)
    assert ttl is not None and 6 * 86400 <= ttl <= 7 * 86400 + 120


def test_player_cannot_change_session_length(base_config):
    client = _client(_app(base_config, run_password="runner99"))
    _, csrf = _login(client, "runner99")
    assert client.post("/api/settings/run-session", json={"run_session_days": 365},
                       headers=csrf).status_code == 403


def test_run_password_must_differ_from_admin(base_config):
    client = _client(_app(base_config))
    _, csrf = _login(client, "s3cret")
    r = client.post("/api/settings/run-password",
                    json={"new_password": "s3cret", "confirm_password": "s3cret"},
                    headers=csrf)
    assert r.status_code == 400
