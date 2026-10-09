"""Settings → Ratings & picking: POST /api/settings/ratings and the live
GET /api/settings/pick-preview, plus GET /api/settings exposing the pick_*
knobs normalized (docs/plans/ratings-weighted-picking.md, Phase 2)."""

import sqlite3

from bpm_tagger.db.ratings import SEED_ADMIN_FROM_PROJECTION_SQL


def _login(client, **body):
    r = client.post("/api/login", json=body or {"password": "s3cret"})
    assert r.status_code == 200, r.get_json()
    return {"X-CSRF-Token": client.get("/api/me").get_json()["csrf_token"]}


def _seed_ratings(db_path, music_dir, rows):
    """rows: (name, rating_or_None)."""
    conn = sqlite3.connect(db_path)
    for name, rating in rows:
        path = f"{music_dir}/{name}.mp3"
        tid = conn.execute(
            "INSERT INTO tracks (file_path, title, status) VALUES (?, ?, 'done')",
            (path, name)).lastrowid
        if rating is not None:
            conn.execute(
                "INSERT INTO track_ratings (owner, track_id, rating, disliked, updated_at) "
                "VALUES ('admin', ?, ?, 0, datetime('now'))", (tid, rating))
    conn.execute(SEED_ADMIN_FROM_PROJECTION_SQL)
    conn.commit()
    conn.close()


# ── GET /api/settings exposes normalized pick_* ─────────────────────────────

def test_settings_get_exposes_defaults(client, base_config):
    _login(client)
    settings = client.get("/api/settings").get_json()["settings"]
    assert settings["pick_use_ratings"] is True
    assert settings["pick_weights"] == [0.1, 0.5, 1.0, 1.0, 3.0, 6.0]
    assert settings["pick_new_factor"] == 1.0


# ── POST /api/settings/ratings ───────────────────────────────────────────────

def test_settings_ratings_saves_and_normalizes(client, base_config, app):
    csrf = _login(client)
    r = client.post("/api/settings/ratings", json={
        "pick_use_ratings": False,
        "pick_weights": [0, -1, 1, 1, 3, 999],
        "pick_new_factor": "more",
    }, headers=csrf)
    assert r.status_code == 200
    body = r.get_json()
    assert body["pick_use_ratings"] is False
    assert body["pick_weights"] == [0.0, 0.0, 1.0, 1.0, 3.0, 100.0]
    assert body["pick_new_factor"] == 2.0

    cfg = app.extensions["state"].config
    assert cfg["pick_use_ratings"] is False
    assert cfg["pick_weights"] == [0.0, 0.0, 1.0, 1.0, 3.0, 100.0]

    settings = client.get("/api/settings").get_json()["settings"]
    assert settings["pick_use_ratings"] is False
    assert settings["pick_new_factor"] == 2.0


def test_settings_ratings_bad_input_falls_back_to_defaults(client, base_config):
    csrf = _login(client)
    r = client.post("/api/settings/ratings",
                    json={"pick_weights": "garbage", "pick_new_factor": "??"},
                    headers=csrf)
    assert r.status_code == 200
    body = r.get_json()
    assert body["pick_weights"] == [0.1, 0.5, 1.0, 1.0, 3.0, 6.0]
    assert body["pick_new_factor"] == 1.0


def test_settings_ratings_requires_csrf_and_admin(client, base_config):
    _login(client)
    assert client.post("/api/settings/ratings", json={}).status_code == 403


def test_settings_ratings_player_forbidden(base_config):
    from bpm_tagger.config import build_config
    from bpm_tagger.web.app import create_app
    import os

    cfg = build_config()
    cfg.update({k: base_config[k] for k in ("db_path", "music_dir", "ui_password", "ui_secret_key")})
    cfg["write_tags"] = False
    cfg["run_password"] = "runner99"
    os.makedirs(cfg["music_dir"], exist_ok=True)
    app = create_app(cfg)
    app.config["TESTING"] = True
    client = app.test_client()
    csrf = _login(client, password="runner99")
    assert client.post("/api/settings/ratings", json={}, headers=csrf).status_code == 403


# ── GET /api/settings/pick-preview ───────────────────────────────────────────

def test_pick_preview_counts_and_share(client, base_config):
    _login(client)
    _seed_ratings(base_config["db_path"], base_config["music_dir"], [
        ("five", 5), ("four", 4), ("three", 3), ("plain1", None), ("plain2", None),
    ])
    r = client.get("/api/settings/pick-preview")
    assert r.status_code == 200
    data = r.get_json()
    assert data["counts"]["5"] == 1
    assert data["counts"]["4"] == 1
    assert data["counts"]["3"] == 1
    # plain1/plain2 are unrated AND unplayed (never scrobbled) — both "new".
    assert data["counts"]["new"] == 2
    assert data["counts"]["unrated"] == 0
    assert abs(sum(data["share"].values()) - 1.0) < 1e-9


def test_pick_preview_honours_query_overrides_without_saving(client, base_config, app):
    _login(client)
    _seed_ratings(base_config["db_path"], base_config["music_dir"], [("a", 5), ("b", None)])
    r = client.get("/api/settings/pick-preview?use_ratings=0")
    assert r.status_code == 200
    assert r.get_json()["use_ratings"] is False
    # Nothing was persisted — the saved config is untouched.
    assert app.extensions["state"].config.get("pick_use_ratings", True) is True


def test_pick_preview_admin_only(base_config):
    from bpm_tagger.config import build_config
    from bpm_tagger.web.app import create_app
    import os

    cfg = build_config()
    cfg.update({k: base_config[k] for k in ("db_path", "music_dir", "ui_password", "ui_secret_key")})
    cfg["write_tags"] = False
    cfg["run_password"] = "runner99"
    os.makedirs(cfg["music_dir"], exist_ok=True)
    app = create_app(cfg)
    app.config["TESTING"] = True
    client = app.test_client()
    _login(client, password="runner99")
    assert client.get("/api/settings/pick-preview").status_code == 403
