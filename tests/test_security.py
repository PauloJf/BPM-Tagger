"""Security-hardening regressions (audit batch):

- SEC-3: session cookie is marked Secure over HTTPS or when explicitly forced.
- SEC-4: a short plaintext env password is flagged as weak (warn, not refuse).
- SEC-5: /healthz library stats are shown to an admin only, never a player.
"""

import os
import sqlite3

from bpm_tagger.config import build_config
from bpm_tagger.web.app import _weak_env_password, create_app


def _app(base_config, **over):
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


def _login(client, password):
    client.post("/api/login", json={"password": password})


def _seed(db_path, music_dir):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO tracks (file_path, title, artist, bpm, status) "
        "VALUES (?, 'song', 'Artist', 150.0, 'done')", (f"{music_dir}/song.mp3",))
    conn.commit()
    conn.close()


# ── SEC-3: Secure cookie ─────────────────────────────────────────────────────

def test_secure_cookie_off_for_plain_http(base_config):
    assert _app(base_config).config["SESSION_COOKIE_SECURE"] is False


def test_secure_cookie_on_for_https_public_url(base_config):
    app = _app(base_config, ui_public_url="https://music.example.com")
    assert app.config["SESSION_COOKIE_SECURE"] is True


def test_secure_cookie_forced(base_config):
    app = _app(base_config, ui_force_secure_cookie=True)
    assert app.config["SESSION_COOKIE_SECURE"] is True


# ── SEC-4: weak env password ─────────────────────────────────────────────────

def test_weak_env_password_flagged():
    assert _weak_env_password({"ui_password": "short"}) is True


def test_strong_env_password_ok():
    assert _weak_env_password({"ui_password": "longenough"}) is False


def test_hashed_password_never_weak():
    # A stored hash is authoritative; the (empty) plaintext isn't scored.
    assert _weak_env_password({"ui_password": "", "ui_password_hash": "x"}) is False


# ── SEC-5: /healthz stats are admin-only ─────────────────────────────────────

def test_healthz_stats_admin_only(base_config):
    app = _app(base_config, run_password="runner99")
    _seed(base_config["db_path"], base_config["music_dir"])

    anon = app.test_client()
    assert "total" not in anon.get("/healthz").get_json()

    admin = app.test_client()
    _login(admin, "s3cret")
    assert "total" in admin.get("/healthz").get_json()   # admin sees library stats

    player = app.test_client()
    _login(player, "runner99")
    body = player.get("/healthz").get_json()
    assert body["status"] == "ok" and "total" not in body   # player does not


# ── /api/changelog: admin-only (What's new popup) ────────────────────────────

def test_changelog_admin_only(base_config):
    app = _app(base_config, run_password="runner99")

    assert app.test_client().get("/api/changelog").status_code == 401   # unauthenticated

    admin = app.test_client()
    _login(admin, "s3cret")
    r = admin.get("/api/changelog")
    assert r.status_code == 200 and "## v" in r.get_json()["changelog"]  # real CHANGELOG.md

    player = app.test_client()
    _login(player, "runner99")
    assert player.get("/api/changelog").status_code == 403   # player scope-gated
