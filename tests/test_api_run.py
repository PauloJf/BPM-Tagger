"""Run mode: starred toggle, /api/run/queue builder, /api/settings/run."""

import sqlite3
from urllib.parse import quote


def _login(client):
    resp = client.post("/api/login", json={"password": "s3cret"})
    assert resp.status_code == 200
    return {"X-CSRF-Token": client.get("/api/me").get_json()["csrf_token"]}


def _seed(db_path: str, music_dir: str, rows):
    """rows: (name, bpm, starred) → done tracks inside the music dir."""
    conn = sqlite3.connect(db_path)
    for name, bpm, starred in rows:
        conn.execute(
            "INSERT INTO tracks (file_path, title, artist, bpm, starred, status) "
            "VALUES (?, ?, 'Artist', ?, ?, 'done')",
            (f"{music_dir}/{name}.mp3", name, bpm, starred))
    conn.commit()
    conn.close()


# ── starred toggle ────────────────────────────────────────────────────────────

def test_star_toggle_roundtrip(client, base_config):
    csrf = _login(client)
    _seed(base_config["db_path"], base_config["music_dir"], [("song", 120.0, 0)])
    path = f"{base_config['music_dir']}/song.mp3"

    r = client.post("/api/track/star", json={"path": path, "starred": True}, headers=csrf)
    assert r.status_code == 200 and r.get_json()["starred"] is True
    assert client.get(f"/api/track?path={quote(path)}").get_json()["track"]["starred"] == 1

    r = client.post("/api/track/star", json={"path": path, "starred": False}, headers=csrf)
    assert r.status_code == 200
    assert client.get(f"/api/track?path={quote(path)}").get_json()["track"]["starred"] == 0


def test_star_requires_csrf_and_known_track(client, base_config):
    csrf = _login(client)
    path = f"{base_config['music_dir']}/nope.mp3"
    assert client.post("/api/track/star",
                       json={"path": path, "starred": True}).status_code in (400, 403)
    assert client.post("/api/track/star", json={"path": path, "starred": True},
                       headers=csrf).status_code == 404


def test_starred_filter_and_count(client, base_config):
    csrf = _login(client)
    _seed(base_config["db_path"], base_config["music_dir"],
          [("a", 120.0, 1), ("b", 130.0, 0)])
    _ = csrf
    data = client.get("/api/tracks?filter=starred").get_json()
    assert data["total"] == 1
    assert data["tracks"][0]["title"] == "a"
    assert data["starred_count"] == 1


# ── dislike toggle ────────────────────────────────────────────────────────────

def test_dislike_toggle_roundtrip(client, base_config):
    csrf = _login(client)
    _seed(base_config["db_path"], base_config["music_dir"], [("song", 120.0, 0)])
    path = f"{base_config['music_dir']}/song.mp3"

    r = client.post("/api/track/dislike", json={"path": path, "disliked": True}, headers=csrf)
    assert r.status_code == 200 and r.get_json()["disliked"] is True
    assert client.get(f"/api/track?path={quote(path)}").get_json()["track"]["disliked"] == 1

    r = client.post("/api/track/dislike", json={"path": path, "disliked": False}, headers=csrf)
    assert r.status_code == 200
    assert client.get(f"/api/track?path={quote(path)}").get_json()["track"]["disliked"] == 0


def test_dislike_requires_csrf_and_known_track(client, base_config):
    csrf = _login(client)
    path = f"{base_config['music_dir']}/nope.mp3"
    assert client.post("/api/track/dislike",
                       json={"path": path, "disliked": True}).status_code in (400, 403)
    assert client.post("/api/track/dislike", json={"path": path, "disliked": True},
                       headers=csrf).status_code == 404


def test_disliked_filter_and_count(client, base_config):
    csrf = _login(client)
    _seed(base_config["db_path"], base_config["music_dir"],
          [("a", 120.0, 0), ("b", 130.0, 0)])
    _ = csrf
    conn = sqlite3.connect(base_config["db_path"])
    conn.execute("UPDATE tracks SET disliked = 1 WHERE title = 'a'")
    conn.commit()
    conn.close()
    data = client.get("/api/tracks?filter=disliked").get_json()
    assert data["total"] == 1
    assert data["tracks"][0]["title"] == "a"
    assert data["disliked_count"] == 1


# ── run queue ─────────────────────────────────────────────────────────────────

def test_run_queue_requires_target(client):
    _login(client)
    assert client.get("/api/run/queue").status_code == 400
    assert client.get("/api/run/queue?bpm=999").status_code == 400


def test_run_queue_octave_folds_and_filters(client, base_config):
    _login(client)
    _seed(base_config["db_path"], base_config["music_dir"], [
        ("exact",   150.0, 0),   # rate 1.0
        ("half",    75.0,  0),   # folds ×2 → native speed
        ("double",  300.0, 0),   # folds ×½ → native speed
        ("near",    147.0, 0),   # 2% stretch
        ("far",     120.0, 0),   # 25% off → excluded
    ])
    conn = sqlite3.connect(base_config["db_path"])
    conn.execute("INSERT INTO tracks (file_path, title, bpm, status) "
                 "VALUES (?, 'gone', 150.0, 'deleted')",
                 (f"{base_config['music_dir']}/gone.mp3",))
    conn.commit()
    conn.close()

    data = client.get("/api/run/queue?bpm=150").get_json()
    by_title = {t["title"]: t for t in data["tracks"]}
    assert set(by_title) == {"exact", "half", "double", "near"}
    assert by_title["exact"]["rate"] == 1.0
    assert by_title["half"]["run_bpm"] == 150.0 and by_title["half"]["rate"] == 1.0
    assert by_title["double"]["run_bpm"] == 150.0 and by_title["double"]["rate"] == 1.0
    assert abs(by_title["near"]["rate"] - 150 / 147) < 1e-3


def test_run_queue_octave_fold_off(client, base_config, app):
    _login(client)
    _seed(base_config["db_path"], base_config["music_dir"],
          [("exact", 150.0, 0), ("half", 75.0, 0)])
    app.extensions["state"].config["run_octave_fold"] = False
    data = client.get("/api/run/queue?bpm=150").get_json()
    assert [t["title"] for t in data["tracks"]] == ["exact"]
    assert data["octave_fold"] is False


def test_run_queue_prefers_starred_within_count(client, base_config):
    _login(client)
    rows = [(f"plain{i}", 150.0, 0) for i in range(10)]
    rows += [(f"fav{i}", 152.0, 1) for i in range(3)]  # worse match but starred
    _seed(base_config["db_path"], base_config["music_dir"], rows)
    data = client.get("/api/run/queue?bpm=150&count=5").get_json()
    titles = {t["title"] for t in data["tracks"]}
    assert len(titles) == 5
    # All three starred tracks selected despite closer unstarred matches.
    assert {"fav0", "fav1", "fav2"} <= titles


def test_run_queue_excludes_disliked_tracks(client, base_config):
    _login(client)
    _seed(base_config["db_path"], base_config["music_dir"], [
        ("liked", 150.0, 0), ("hated", 150.0, 0),
    ])
    conn = sqlite3.connect(base_config["db_path"])
    conn.execute("UPDATE tracks SET disliked = 1 WHERE title = 'hated'")
    conn.commit()
    conn.close()
    data = client.get("/api/run/queue?bpm=150").get_json()
    assert {t["title"] for t in data["tracks"]} == {"liked"}


def test_run_queue_get_response_has_recycled_false(client, base_config):
    _login(client)
    _seed(base_config["db_path"], base_config["music_dir"], [("a", 150.0, 0)])
    data = client.get("/api/run/queue?bpm=150").get_json()
    assert data["recycled"] is False
    assert {t["title"] for t in data["tracks"]} == {"a"}


def test_run_queue_post_excludes_paths(client, base_config):
    _login(client)
    _seed(base_config["db_path"], base_config["music_dir"], [
        ("a", 150.0, 0), ("b", 150.0, 0), ("c", 150.0, 0),
    ])
    exclude = [f"{base_config['music_dir']}/a.mp3"]
    data = client.post("/api/run/queue", json={"bpm": 150, "exclude": exclude}).get_json()
    assert {t["title"] for t in data["tracks"]} == {"b", "c"}
    assert data["recycled"] is False


def test_run_queue_recycles_when_exclude_exhausts_pool(client, base_config):
    _login(client)
    _seed(base_config["db_path"], base_config["music_dir"], [
        ("a", 150.0, 0), ("b", 150.0, 0),
    ])
    exclude = [f"{base_config['music_dir']}/a.mp3", f"{base_config['music_dir']}/b.mp3"]
    data = client.post("/api/run/queue", json={"bpm": 150, "exclude": exclude}).get_json()
    # Every match was excluded — the server recycles the full pool rather than
    # returning an empty batch.
    assert {t["title"] for t in data["tracks"]} == {"a", "b"}
    assert data["recycled"] is True


def test_run_queue_post_bad_exclude_type(client, base_config):
    _login(client)
    _seed(base_config["db_path"], base_config["music_dir"], [("a", 150.0, 0)])
    r = client.post("/api/run/queue", json={"bpm": 150, "exclude": "not-a-list"})
    assert r.status_code == 400


def test_run_queue_post_requires_target(client):
    _login(client)
    r = client.post("/api/run/queue", json={"exclude": []})
    assert r.status_code == 400


# ── run settings ──────────────────────────────────────────────────────────────

def test_settings_run_sanitizes_and_persists(client, base_config, app):
    csrf = _login(client)
    r = client.post("/api/settings/run", json={
        "run_presets": [
            {"name": "Sprint", "bpm": 1000},        # bpm clamped to 300
            {"name": "", "bpm": 10},                # empty name → default, bpm → 30
            150,                                    # legacy plain number
            {"name": "X" * 40, "bpm": "junk"},      # name truncated, bpm → default
        ],
        "run_octave_fold": False,
        "run_prefer_starred": False,
        "run_queue_size": 9999,
        "run_tolerance_pct": 2.5,
        "run_stretch_limit_pct": 0,
    }, headers=csrf)
    assert r.status_code == 200
    cfg = app.extensions["state"].config
    assert cfg["run_presets"] == [
        {"name": "Sprint", "bpm": 300},
        {"name": "Easy", "bpm": 30},
        {"name": "Steady", "bpm": 150},
        {"name": "X" * 20, "bpm": 175},
    ]
    assert cfg["run_octave_fold"] is False
    assert cfg["run_prefer_starred"] is False
    assert cfg["run_queue_size"] == 200                # clamped
    assert cfg["run_tolerance_pct"] == 2.5
    assert cfg["run_stretch_limit_pct"] == 1.0         # clamped

    settings = client.get("/api/settings").get_json()["settings"]
    assert settings["run_presets"][0] == {"name": "Sprint", "bpm": 300}


def test_settings_run_short_list_pads_defaults(client):
    csrf = _login(client)
    r = client.post("/api/settings/run", json={"run_presets": [{"name": "Hill", "bpm": 172}]},
                    headers=csrf)
    assert r.status_code == 200
    presets = client.get("/api/settings").get_json()["settings"]["run_presets"]
    assert presets == [
        {"name": "Hill", "bpm": 172},
        {"name": "Easy", "bpm": 155},
        {"name": "Steady", "bpm": 165},
        {"name": "Tempo", "bpm": 175},
    ]
