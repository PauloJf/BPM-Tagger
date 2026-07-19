"""Phase 5 — local player users: auth, per-user scoping, admin panel."""

import sqlite3

import pytest

from bpm_tagger.web.app import create_app


def _admin(app):
    c = app.test_client()
    assert c.post("/api/login", json={"password": "s3cret"}).status_code == 200
    return c, {"X-CSRF-Token": c.get("/api/me").get_json()["csrf_token"]}


def _login(app, **body):
    c = app.test_client()
    r = c.post("/api/login", json=body)
    return c, r


def _seed_playlist(db_path, music_dir, name, bpm=150.0, source="local"):
    """Create a playlist with one matched, analyzed local track. Returns playlist id."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    path = f"{music_dir}/{name}.mp3"
    conn.execute(
        "INSERT INTO tracks (file_path, title, artist, bpm, status) "
        "VALUES (?, ?, 'Artist', ?, 'done')", (path, name, bpm))
    cur = conn.execute(
        "INSERT INTO playlists (source, name, enabled, created_at) VALUES (?, ?, 1, '')",
        (source, name))
    pid = cur.lastrowid
    conn.execute(
        "INSERT INTO playlist_tracks (playlist_id, source_track_id, title, artist, "
        "match_status, matched_file_path, position) VALUES (?, ?, ?, 'Artist', 'have', ?, 0)",
        (pid, path, name, path))
    conn.commit()
    conn.close()
    return pid


# ── admin user CRUD ─────────────────────────────────────────────────────────
def test_create_and_list_player(client, app):
    c, csrf = _admin(app)
    r = c.post("/api/players",
               json={"username": "Runner", "password": "runrunrun", "full_access": False},
               headers=csrf)
    assert r.status_code == 200
    user = r.get_json()["player"]
    assert user["username"] == "runner"           # stored lowercased
    assert user["full_access"] is False
    assert "password_hash" not in user            # never leaked
    listing = c.get("/api/players", headers=csrf).get_json()["players"]
    assert [u["username"] for u in listing] == ["runner"]


def test_create_player_rejects_short_password_and_dup(client, app):
    c, csrf = _admin(app)
    assert c.post("/api/players", json={"username": "a", "password": "short"},
                  headers=csrf).status_code == 400
    assert c.post("/api/players", json={"username": "a", "password": "longenough"},
                  headers=csrf).status_code == 200
    # Duplicate username → 409.
    assert c.post("/api/players", json={"username": "A", "password": "longenough"},
                  headers=csrf).status_code == 409


def test_create_player_rejects_admin_password(client, app):
    c, csrf = _admin(app)
    # A player password identical to the admin password is refused.
    assert c.post("/api/players", json={"username": "x", "password": "s3cret"},
                  headers=csrf).status_code == 400


# ── login as a player user ────────────────────────────────────────────────────
def test_player_login_and_identity(client, app):
    c, csrf = _admin(app)
    c.post("/api/players", json={"username": "runner", "password": "runrunrun"},
           headers=csrf)
    pc, r = _login(app, username="runner", password="runrunrun")
    assert r.status_code == 200 and r.get_json()["role"] == "player"
    me = pc.get("/api/me").get_json()
    assert me["role"] == "player"
    assert me["username"] == "runner"
    # Named player users are always playlist-scoped, never full-access.
    assert me["full_access"] is False


def test_player_login_wrong_and_disabled(client, app):
    c, csrf = _admin(app)
    pid = c.post("/api/players", json={"username": "runner", "password": "runrunrun"},
                 headers=csrf).get_json()["player"]["id"]
    # Wrong password.
    assert _login(app, username="runner", password="nope")[1].status_code == 401
    # Disable → login refused.
    assert c.patch(f"/api/players/{pid}", json={"enabled": False},
                   headers=csrf).status_code == 200
    assert _login(app, username="runner", password="runrunrun")[1].status_code == 401


def test_blank_username_still_logs_in_admin(client, app):
    # Back-compat: no username → the password-only admin flow, unchanged.
    _, r = _login(app, password="s3cret")
    assert r.status_code == 200 and r.get_json()["role"] == "admin"


def test_password_reset_invalidates_session(client, app):
    c, csrf = _admin(app)
    pid = c.post("/api/players", json={"username": "runner", "password": "runrunrun"},
                 headers=csrf).get_json()["player"]["id"]
    pc, r = _login(app, username="runner", password="runrunrun")
    assert pc.get("/api/me").get_json()["authenticated"] is True
    # Reset the password → the live session's stamp no longer matches.
    assert c.post(f"/api/players/{pid}/password", json={"new_password": "brandnew1"},
                  headers=csrf).status_code == 200
    # A protected endpoint now 401s for the old session.
    assert pc.get("/api/run/playlists").status_code == 401


def test_delete_player_invalidates_session(client, app):
    c, csrf = _admin(app)
    pid = c.post("/api/players", json={"username": "runner", "password": "runrunrun"},
                 headers=csrf).get_json()["player"]["id"]
    pc, _ = _login(app, username="runner", password="runrunrun")
    assert c.delete(f"/api/players/{pid}", headers=csrf).status_code == 200
    assert pc.get("/api/run/playlists").status_code == 401


# ── per-user scoping ──────────────────────────────────────────────────────────
def test_restricted_player_sees_only_its_playlists(client, app, base_config):
    c, csrf = _admin(app)
    a = _seed_playlist(base_config["db_path"], base_config["music_dir"], "a")
    b = _seed_playlist(base_config["db_path"], base_config["music_dir"], "b")
    pid = c.post("/api/players", json={"username": "runner", "password": "runrunrun",
                                       "full_access": False, "playlist_ids": [a]},
                 headers=csrf).get_json()["player"]["id"]
    _ = pid
    pc, _ = _login(app, username="runner", password="runrunrun")
    pls = pc.get("/api/run/playlists").get_json()["playlists"]
    assert {p["id"] for p in pls} == {a}
    # Can run its own playlist…
    assert pc.get(f"/api/run/queue?bpm=150&playlist={a}").status_code == 200
    # …but not another playlist, and not the whole-library pool.
    assert pc.get(f"/api/run/queue?bpm=150&playlist={b}").status_code == 403
    assert pc.get("/api/run/queue?bpm=150").status_code == 403


def test_named_player_is_always_scoped_even_if_full_access_requested(client, app, base_config):
    """`full_access` is no longer honored for named users — a full-library non-admin
    login is the shared Guest login only, so requesting it on a player is ignored."""
    c, csrf = _admin(app)
    _a = _seed_playlist(base_config["db_path"], base_config["music_dir"], "a")
    _b = _seed_playlist(base_config["db_path"], base_config["music_dir"], "b")
    # Even asking for full_access: True yields a scoped user with no playlists.
    c.post("/api/players", json={"username": "full", "password": "fullpass1",
                                 "full_access": True}, headers=csrf)
    pc, _ = _login(app, username="full", password="fullpass1")
    assert pc.get("/api/me").get_json()["full_access"] is False
    # No playlists assigned → sees none, and the whole-library pool is forbidden.
    assert pc.get("/api/run/playlists").get_json()["playlists"] == []
    assert pc.get("/api/run/queue?bpm=150").status_code == 403


def test_guest_is_full_access(app_with_guest, base_config):
    app = app_with_guest
    a = _seed_playlist(base_config["db_path"], base_config["music_dir"], "a")
    gc, r = _login(app, password="guestpass")     # blank username → shared guest
    assert r.status_code == 200 and r.get_json()["role"] == "player"
    me = gc.get("/api/me").get_json()
    assert me["full_access"] is True and me["username"] is None
    assert {p["id"] for p in gc.get("/api/run/playlists").get_json()["playlists"]} == {a}
    assert gc.get("/api/run/queue?bpm=150").status_code == 200


# ── admin-only enforcement + cascades ─────────────────────────────────────────
def test_player_forbidden_on_admin_endpoints(client, app):
    c, csrf = _admin(app)
    c.post("/api/players", json={"username": "runner", "password": "runrunrun"},
           headers=csrf)
    pc, _ = _login(app, username="runner", password="runrunrun")
    assert pc.get("/api/players").status_code == 403
    assert pc.post("/api/players", json={"username": "x", "password": "longenough"}).status_code == 403


def test_delete_playlist_cascades_player_playlists(client, app, base_config):
    c, csrf = _admin(app)
    a = _seed_playlist(base_config["db_path"], base_config["music_dir"], "a")
    pid = c.post("/api/players", json={"username": "runner", "password": "runrunrun",
                                       "playlist_ids": [a]},
                 headers=csrf).get_json()["player"]["id"]
    st = app.extensions["state"]
    assert st.db.playlist_ids_for_player(pid) == {a}
    st.db.delete_playlist(a)
    assert st.db.playlist_ids_for_player(pid) == set()


@pytest.fixture
def app_with_guest(base_config):
    import os
    os.makedirs(base_config["music_dir"], exist_ok=True)
    cfg = dict(base_config)
    cfg["run_password"] = "guestpass"
    return create_app(cfg)
