"""Cross-device player state: the SPA's queue snapshot stored per account.

- The admin stores one snapshot shared by every admin session/device.
- Named player users each store their own.
- The shared Guest (RUN_PASSWORD) login has no account row → sync: false and
  the SPA stays on per-browser localStorage.
"""

import json
import os

import pytest

from bpm_tagger.db import BPMDatabase


# ── DB layer ────────────────────────────────────────────────────────────────
@pytest.fixture
def db(tmp_path):
    return BPMDatabase(str(tmp_path / "bpm.db"))


def test_player_state_roundtrip_and_upsert(db):
    assert db.get_player_state("admin") is None
    stamp1 = db.save_player_state("admin", '{"queue":[1]}')
    row = db.get_player_state("admin")
    assert row["state"] == '{"queue":[1]}' and row["updated_at"] == stamp1
    stamp2 = db.save_player_state("admin", '{"queue":[2]}')
    row = db.get_player_state("admin")
    assert row["state"] == '{"queue":[2]}' and row["updated_at"] == stamp2
    db.clear_player_state("admin")
    assert db.get_player_state("admin") is None


def test_player_state_is_per_owner(db):
    db.save_player_state("admin", '{"queue":["a"]}')
    db.save_player_state("player:1", '{"queue":["p"]}')
    assert json.loads(db.get_player_state("admin")["state"]) == {"queue": ["a"]}
    assert json.loads(db.get_player_state("player:1")["state"]) == {"queue": ["p"]}


def test_delete_player_clears_its_state(db):
    pid = db.add_player("runner", "hash")
    db.save_player_state(f"player:{pid}", '{"queue":[]}')
    db.delete_player(pid)
    assert db.get_player_state(f"player:{pid}") is None


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


SNAPSHOT = {"queue": [{"path": "/music/a.mp3", "title": "A"}], "order": [0], "pos": 0,
            "shuffle": False, "repeat": "off", "volume": 1, "time": 42.5, "playing": True}


def test_admin_state_roundtrip(base_config):
    app = _app(base_config)
    client = app.test_client()
    _, csrf = _login(client, password="s3cret")

    # Nothing stored yet — but the account syncs.
    r = client.get("/api/player/state")
    assert r.status_code == 200
    assert r.get_json() == {"sync": True, "state": None, "updated_at": None}

    r = client.put("/api/player/state", json={"state": SNAPSHOT}, headers=csrf)
    body = r.get_json()
    assert r.status_code == 200 and body["ok"] and body["sync"] and body["updated_at"]

    r = client.get("/api/player/state").get_json()
    assert r["state"] == SNAPSHOT and r["updated_at"] == body["updated_at"]

    # Another admin "device" sees the same snapshot.
    other = app.test_client()
    _login(other, password="s3cret")
    assert other.get("/api/player/state").get_json()["state"] == SNAPSHOT

    # state: null clears it.
    r = client.put("/api/player/state", json={"state": None}, headers=csrf)
    assert r.status_code == 200 and r.get_json()["updated_at"] is None
    assert client.get("/api/player/state").get_json()["state"] is None


def test_player_user_state_is_own_row(base_config):
    app = _app(base_config)
    admin = app.test_client()
    _, acsrf = _login(admin, password="s3cret")
    admin.post("/api/players", json={"username": "runner", "password": "runrunrun"}, headers=acsrf)
    admin.put("/api/player/state", json={"state": SNAPSHOT}, headers=acsrf)

    player = app.test_client()
    _, pcsrf = _login(player, username="runner", password="runrunrun")
    # The player's own slot starts empty — it never sees the admin's queue.
    assert player.get("/api/player/state").get_json()["state"] is None

    mine = dict(SNAPSHOT, queue=[{"path": "/music/b.mp3", "title": "B"}])
    r = player.put("/api/player/state", json={"state": mine}, headers=pcsrf)
    assert r.status_code == 200 and r.get_json()["sync"] is True

    # A fresh login on another "device" sees it; the admin's copy is untouched.
    other = app.test_client()
    _login(other, username="runner", password="runrunrun")
    assert other.get("/api/player/state").get_json()["state"] == mine
    assert admin.get("/api/player/state").get_json()["state"] == SNAPSHOT


def test_guest_state_is_noop(base_config):
    app = _app(base_config, run_password="runner99")
    guest = app.test_client()
    r, gcsrf = _login(guest, password="runner99")
    assert r.get_json()["role"] == "player"          # shared Guest → player role, no account

    r = guest.get("/api/player/state")
    assert r.status_code == 200 and r.get_json()["sync"] is False

    r = guest.put("/api/player/state", json={"state": SNAPSHOT}, headers=gcsrf)
    assert r.status_code == 200 and r.get_json()["sync"] is False

    # Nothing was stored under any owner.
    admin = app.test_client()
    _, _ = _login(admin, password="s3cret")
    assert admin.get("/api/player/state").get_json()["state"] is None


def test_state_rejects_malformed_snapshots(base_config):
    app = _app(base_config)
    client = app.test_client()
    _, csrf = _login(client, password="s3cret")
    for bad in ("a string", 42, ["a", "list"], {"no_queue": True}, {"queue": "nope"}):
        r = client.put("/api/player/state", json={"state": bad}, headers=csrf)
        assert r.status_code == 400, bad


def test_state_rejects_oversized_snapshot(base_config):
    app = _app(base_config)
    client = app.test_client()
    _, csrf = _login(client, password="s3cret")
    huge = {"queue": [{"path": "x" * 1000, "title": "t"} for _ in range(600)]}
    r = client.put("/api/player/state", json={"state": huge}, headers=csrf)
    assert r.status_code == 413


def test_state_requires_auth_and_csrf(base_config):
    app = _app(base_config)
    client = app.test_client()
    assert client.get("/api/player/state").status_code == 401
    assert client.put("/api/player/state", json={"state": SNAPSHOT}).status_code == 401
    _login(client, password="s3cret")
    # Authenticated but missing CSRF header → 403 (GET needs no token).
    assert client.put("/api/player/state", json={"state": SNAPSHOT}).status_code == 403
    assert client.get("/api/player/state").status_code == 200
