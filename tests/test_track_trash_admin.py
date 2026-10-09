"""The track page's Delete button (POST /api/track/trash) is admin-only.

The route is protected by the player-scope allowlist in web/app.py, not by its
own check, so this pins that a future allowlist edit can't hand deletion to a
run-only (player) session."""

import os

from bpm_tagger.config import build_config


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


def _login(client, password):
    client.post("/api/login", json={"password": password})
    return {"X-CSRF-Token": client.get("/api/me").get_json()["csrf_token"]}


def _song(base_config):
    path = os.path.join(base_config["music_dir"], "song.mp3")
    os.makedirs(base_config["music_dir"], exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"\x00")
    return path


def test_player_role_cannot_trash_a_track(base_config):
    path = _song(base_config)
    client = _app(base_config, run_password="runpw").test_client()
    csrf = _login(client, "runpw")
    r = client.post("/api/track/trash", json={"file_path": path}, headers=csrf)
    assert r.status_code == 403
    assert os.path.exists(path)  # untouched


def test_admin_can_trash_a_track(base_config):
    path = _song(base_config)
    client = _app(base_config, run_password="runpw").test_client()
    csrf = _login(client, "s3cret")
    r = client.post("/api/track/trash", json={"file_path": path}, headers=csrf)
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert not os.path.exists(path)
