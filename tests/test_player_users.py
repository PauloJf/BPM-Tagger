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


def test_restricted_player_topup_stays_in_scope(client, app, base_config):
    """A scoped player running a thin playlist tops up only from its own playlists,
    never the whole library (regression: _build padded from get_run_candidates(None))."""
    c, csrf = _admin(app)
    # The player's own playlist has one runnable track at 150 BPM.
    mine = _seed_playlist(base_config["db_path"], base_config["music_dir"], "mine", bpm=150.0)
    # A same-cadence track that lives only in a playlist the player can't reach —
    # a tempting top-up candidate the old code would have pulled in.
    _other = _seed_playlist(base_config["db_path"], base_config["music_dir"], "other", bpm=150.0)
    c.post("/api/players", json={"username": "runner", "password": "runrunrun",
                                 "playlist_ids": [mine]}, headers=csrf)
    pc, _ = _login(app, username="runner", password="runrunrun")
    # Ask for a queue far larger than the one in-scope track: the top-up must stay
    # scoped, so the out-of-scope track never appears.
    r = pc.get(f"/api/run/queue?bpm=150&count=20&playlist={mine}")
    assert r.status_code == 200
    paths = [t["path"] for t in r.get_json()["tracks"]]
    assert paths and all(p.endswith("/mine.mp3") for p in paths)


def test_pooled_source_runs_union_of_players_playlists(client, app, base_config):
    """`playlist=mine` pools every playlist the scoped player may run — and nothing
    else. Tracks from both assigned playlists appear; an out-of-scope one never does."""
    c, csrf = _admin(app)
    a = _seed_playlist(base_config["db_path"], base_config["music_dir"], "a", bpm=150.0)
    b = _seed_playlist(base_config["db_path"], base_config["music_dir"], "b", bpm=150.0)
    _out = _seed_playlist(base_config["db_path"], base_config["music_dir"], "out", bpm=150.0)
    c.post("/api/players", json={"username": "runner", "password": "runrunrun",
                                 "playlist_ids": [a, b]}, headers=csrf)
    pc, _ = _login(app, username="runner", password="runrunrun")
    r = pc.get("/api/run/queue?bpm=150&count=20&playlist=mine")
    assert r.status_code == 200
    names = {p.rsplit("/", 1)[-1] for p in (t["path"] for t in r.get_json()["tracks"])}
    assert names == {"a.mp3", "b.mp3"}      # both assigned playlists, never out.mp3


def test_named_player_with_full_access_sees_the_whole_library(client, app, base_config):
    """An admin can give a named player the whole library (it used to be the shared Guest only)."""
    c, csrf = _admin(app)
    a = _seed_playlist(base_config["db_path"], base_config["music_dir"], "a")
    c.post("/api/players", json={"username": "full", "password": "fullpass1",
                                 "full_access": True}, headers=csrf)
    pc, _ = _login(app, username="full", password="fullpass1")
    assert pc.get("/api/me").get_json()["full_access"] is True
    assert {p["id"] for p in pc.get("/api/run/playlists").get_json()["playlists"]} == {a}
    assert pc.get("/api/run/queue?bpm=150").status_code == 200


def test_full_access_can_be_toggled_and_defaults_off(client, app, base_config):
    c, csrf = _admin(app)
    _seed_playlist(base_config["db_path"], base_config["music_dir"], "a")
    pid = c.post("/api/players", json={"username": "scoped", "password": "scopedpw1"},
                 headers=csrf).get_json()["player"]["id"]
    pc, _ = _login(app, username="scoped", password="scopedpw1")
    assert pc.get("/api/me").get_json()["full_access"] is False
    assert pc.get("/api/run/queue?bpm=150").status_code == 403
    c.patch(f"/api/players/{pid}", json={"full_access": True}, headers=csrf)
    assert pc.get("/api/run/queue?bpm=150").status_code == 200


def test_member_role_browses_but_cannot_administer(client, app, base_config):
    c, csrf = _admin(app)
    _seed_playlist(base_config["db_path"], base_config["music_dir"], "a")
    r = c.post("/api/players", json={"username": "ana", "password": "anapass12", "role": "member"},
               headers=csrf)
    assert r.get_json()["player"]["role"] == "member"
    mc, lr = _login(app, username="ana", password="anapass12")
    assert lr.get_json()["role"] == "member"
    me = mc.get("/api/me").get_json()
    assert me["role"] == "member" and me["username"] == "ana" and me["full_access"] is True
    for path in ("/api/tracks", "/api/artists", "/api/albums", "/api/playlists",
                 "/api/run/queue?bpm=150", "/api/settings"):
        assert mc.get(path).status_code == 200, path
    # Settings come back filtered (no Navidrome host, no secrets), like a player's.
    assert "navidrome_url" not in mc.get("/api/settings").get_json()["settings"]
    mtok = mc.get("/api/me").get_json()["csrf_token"]
    for method, path in (("get", "/api/players"), ("get", "/api/review"), ("get", "/api/duplicates"),
                         ("get", "/api/stats"), ("post", "/api/scan/start"),
                         ("post", "/api/playlists"), ("post", "/api/settings/scan")):
        resp = getattr(mc, method)(path, json={}, headers={"X-CSRF-Token": mtok})
        assert resp.status_code == 403, path


def test_member_ratings_are_their_own(client, app, base_config):
    from bpm_tagger.web.auth import session_owner  # noqa: F401  (owner key is player:<id>)
    c, csrf = _admin(app)
    pid = c.post("/api/players", json={"username": "ana", "password": "anapass12", "role": "member"},
                 headers=csrf).get_json()["player"]["id"]
    mc, _ = _login(app, username="ana", password="anapass12")
    with mc.session_transaction() as s:
        assert s["role"] == "member" and s["player_id"] == pid


def test_changing_role_logs_the_account_out(client, app):
    c, csrf = _admin(app)
    pid = c.post("/api/players", json={"username": "ana", "password": "anapass12"},
                 headers=csrf).get_json()["player"]["id"]
    pc, _ = _login(app, username="ana", password="anapass12")
    assert pc.get("/api/run/playlists").status_code == 200
    c.patch(f"/api/players/{pid}", json={"role": "member"}, headers=csrf)
    assert pc.get("/api/run/playlists").status_code == 401
    assert c.patch(f"/api/players/{pid}", json={"role": "boss"}, headers=csrf).status_code == 400


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


def test_member_cannot_use_curation_filters(client, app, base_config):
    c, csrf = _admin(app)
    _seed_playlist(base_config["db_path"], base_config["music_dir"], "a")
    conn = sqlite3.connect(base_config["db_path"])
    conn.execute("INSERT INTO tracks (file_path, title, artist, status) VALUES ('/x/gone.mp3', 'gone', 'A', 'deleted')")
    conn.commit()
    conn.close()
    c.post("/api/players", json={"username": "ana", "password": "anapass12", "role": "member"}, headers=csrf)
    mc, _ = _login(app, username="ana", password="anapass12")
    admin_deleted = c.get("/api/tracks?filter=deleted").get_json()
    member_deleted = mc.get("/api/tracks?filter=deleted").get_json()
    assert [t["file_path"] for t in admin_deleted["tracks"]] == ["/x/gone.mp3"]
    assert "/x/gone.mp3" not in [t["file_path"] for t in member_deleted["tracks"]]
