"""Listen mode: the regular (non-cadence) playlist queue + the kiosk gate.

/api/listen/queue returns every playable track of one playlist (or the pooled
"mine" source) — no BPM required, unlike the run queue. Availability for the
player role follows the admin's ``player_listen_mode`` setting: the endpoint is
in the default-deny allowlist, and 403s player sessions itself while the mode
is off. Admin sessions are never gated.
"""

import os
import sqlite3


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


def _login(app, **body):
    c = app.test_client()
    r = c.post("/api/login", json=body)
    csrf = None
    if r.status_code == 200:
        csrf = {"X-CSRF-Token": c.get("/api/me").get_json()["csrf_token"]}
    return c, r, csrf


def _seed_playlist(db_path, music_dir, name, tracks):
    """A playlist with the given (title, bpm_or_None) local tracks, all matched.
    Returns the playlist id."""
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO playlists (source, name, enabled, created_at) VALUES ('local', ?, 1, '')",
        (name,))
    pid = cur.lastrowid
    for i, (title, bpm) in enumerate(tracks):
        path = f"{music_dir}/{name}-{title}.mp3"
        conn.execute(
            "INSERT INTO tracks (file_path, title, artist, bpm, status, starred) "
            "VALUES (?, ?, 'Artist', ?, 'done', ?)", (path, title, bpm, int(i == 0)))
        conn.execute(
            "INSERT INTO playlist_tracks (playlist_id, source_track_id, title, artist, "
            "match_status, matched_file_path, position) VALUES (?, ?, ?, 'Artist', 'have', ?, ?)",
            (pid, path, title, path, i))
    conn.commit()
    conn.close()
    return pid


# ── shape: playable means "matched a local file", BPM not required ────────────

def test_admin_gets_playlist_tracks_bpm_not_required(base_config):
    app = _app(base_config)
    pid = _seed_playlist(base_config["db_path"], base_config["music_dir"], "mix",
                         [("one", 150.0), ("two", None)])
    c, _, _ = _login(app, password="s3cret")
    r = c.get(f"/api/listen/queue?playlist={pid}")
    assert r.status_code == 200
    body = r.get_json()
    # Both tracks are playable — the un-analyzed one included (the run queue
    # would have dropped it).
    assert body["count"] == 2
    titles = [t["title"] for t in body["tracks"]]
    assert titles == ["one", "two"]              # playlist order preserved
    assert body["tracks"][0]["starred"] is True
    assert body["tracks"][1]["bpm"] is None


def test_listen_queue_param_validation(base_config):
    app = _app(base_config)
    c, _, _ = _login(app, password="s3cret")
    assert c.get("/api/listen/queue").status_code == 400
    assert c.get("/api/listen/queue?playlist=abc").status_code == 400
    assert c.get("/api/listen/queue?playlist=9999").status_code == 404


# ── the whole-library source ──────────────────────────────────────────────────

def test_library_source_returns_everything_playable(base_config):
    app = _app(base_config)
    _seed_playlist(base_config["db_path"], base_config["music_dir"], "mix",
                   [("one", 150.0), ("two", None)])
    # A library track on no playlist at all is still in the library source.
    conn = sqlite3.connect(base_config["db_path"])
    conn.execute("INSERT INTO tracks (file_path, title, artist, bpm, status) "
                 "VALUES (?, 'loose', 'Artist', NULL, 'done')",
                 (f"{base_config['music_dir']}/loose.mp3",))
    conn.commit(); conn.close()
    c, _, _ = _login(app, password="s3cret")
    r = c.get("/api/listen/queue?playlist=library")
    assert r.status_code == 200
    body = r.get_json()
    assert body["playlist"] == "library"
    assert body["count"] == 3
    assert {t["title"] for t in body["tracks"]} == {"one", "two", "loose"}


def test_library_source_forbidden_for_scoped_player(base_config):
    app = _app(base_config, player_listen_mode="on")
    mine = _seed_playlist(base_config["db_path"], base_config["music_dir"], "mine-pl",
                          [("one", 150.0)])
    admin, _, csrf = _login(app, password="s3cret")
    assert admin.post("/api/players",
                      json={"username": "runner", "password": "runrunrun",
                            "playlist_ids": [mine]},
                      headers=csrf).status_code == 200
    c, _, _ = _login(app, username="runner", password="runrunrun")
    assert c.get("/api/listen/queue?playlist=library").status_code == 403


def test_library_source_allowed_for_full_access_guest(base_config):
    # The shared Guest login is full-access, like on the Run source picker.
    app = _app(base_config, run_password="runner99", player_listen_mode="on")
    _seed_playlist(base_config["db_path"], base_config["music_dir"], "mix",
                   [("one", 150.0)])
    c, _, _ = _login(app, password="runner99")
    r = c.get("/api/listen/queue?playlist=library")
    assert r.status_code == 200 and r.get_json()["count"] == 1


# ── the kiosk gate: player_listen_mode ────────────────────────────────────────

def test_player_403_while_mode_off(base_config):
    app = _app(base_config, run_password="runner99")   # mode defaults to off
    pid = _seed_playlist(base_config["db_path"], base_config["music_dir"], "mix",
                         [("one", 150.0)])
    c, r, _ = _login(app, password="runner99")
    assert r.get_json()["role"] == "player"
    assert c.get(f"/api/listen/queue?playlist={pid}").status_code == 403


def test_player_allowed_when_mode_on(base_config):
    app = _app(base_config, run_password="runner99", player_listen_mode="on")
    pid = _seed_playlist(base_config["db_path"], base_config["music_dir"], "mix",
                         [("one", 150.0)])
    c, _, _ = _login(app, password="runner99")
    r = c.get(f"/api/listen/queue?playlist={pid}")
    assert r.status_code == 200 and r.get_json()["count"] == 1


def test_admin_never_gated_by_mode(base_config):
    app = _app(base_config)                             # mode off
    pid = _seed_playlist(base_config["db_path"], base_config["music_dir"], "mix",
                         [("one", 150.0)])
    c, _, _ = _login(app, password="s3cret")
    assert c.get(f"/api/listen/queue?playlist={pid}").status_code == 200


def test_me_reports_listen_mode(base_config):
    app = _app(base_config, run_password="runner99", player_listen_mode="default")
    c, _, _ = _login(app, password="runner99")
    assert c.get("/api/me").get_json()["listen_mode"] == "default"


# ── per-user scoping (named player users) ─────────────────────────────────────

def test_named_player_scoped_to_own_playlists(base_config):
    app = _app(base_config, player_listen_mode="on")
    mine = _seed_playlist(base_config["db_path"], base_config["music_dir"], "mine-pl",
                          [("one", 150.0)])
    other = _seed_playlist(base_config["db_path"], base_config["music_dir"], "other-pl",
                           [("two", 120.0)])
    admin, _, csrf = _login(app, password="s3cret")
    assert admin.post("/api/players",
                      json={"username": "runner", "password": "runrunrun",
                            "playlist_ids": [mine]},
                      headers=csrf).status_code == 200
    c, r, _ = _login(app, username="runner", password="runrunrun")
    assert r.status_code == 200
    assert c.get(f"/api/listen/queue?playlist={mine}").status_code == 200
    assert c.get(f"/api/listen/queue?playlist={other}").status_code == 403
    # The pooled source unions only the player's own playlists.
    pooled = c.get("/api/listen/queue?playlist=mine").get_json()
    assert [t["title"] for t in pooled["tracks"]] == ["one"]


# ── the admin setting endpoint ────────────────────────────────────────────────

def test_admin_sets_listen_mode(base_config):
    app = _app(base_config, run_password="runner99")
    admin, _, csrf = _login(app, password="s3cret")
    r = admin.post("/api/settings/listen-mode", json={"player_listen_mode": "only"},
                   headers=csrf)
    assert r.status_code == 200 and r.get_json()["player_listen_mode"] == "only"
    # Applies live: a player session now passes the gate.
    pid = _seed_playlist(base_config["db_path"], base_config["music_dir"], "mix",
                         [("one", 150.0)])
    c, _, _ = _login(app, password="runner99")
    assert c.get(f"/api/listen/queue?playlist={pid}").status_code == 200
    assert c.get("/api/me").get_json()["listen_mode"] == "only"


def test_listen_mode_validation_and_player_forbidden(base_config):
    app = _app(base_config, run_password="runner99")
    admin, _, csrf = _login(app, password="s3cret")
    assert admin.post("/api/settings/listen-mode", json={"player_listen_mode": "sideways"},
                      headers=csrf).status_code == 400
    p, _, pcsrf = _login(app, password="runner99")
    assert p.post("/api/settings/listen-mode", json={"player_listen_mode": "on"},
                  headers=pcsrf).status_code == 403
