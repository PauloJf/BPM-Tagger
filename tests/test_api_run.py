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


# ── run settings ──────────────────────────────────────────────────────────────

def test_settings_run_sanitizes_and_persists(client, base_config, app):
    csrf = _login(client)
    r = client.post("/api/settings/run", json={
        "run_presets": [1000, 10, "junk", 165],
        "run_octave_fold": False,
        "run_prefer_starred": False,
        "run_queue_size": 9999,
        "run_tolerance_pct": 2.5,
        "run_stretch_limit_pct": 0,
    }, headers=csrf)
    assert r.status_code == 200
    cfg = app.extensions["state"].config
    assert cfg["run_presets"] == [300, 30, 160, 165]   # clamped / default-filled
    assert cfg["run_octave_fold"] is False
    assert cfg["run_prefer_starred"] is False
    assert cfg["run_queue_size"] == 200                # clamped
    assert cfg["run_tolerance_pct"] == 2.5
    assert cfg["run_stretch_limit_pct"] == 1.0         # clamped

    settings = client.get("/api/settings").get_json()["settings"]
    assert settings["run_presets"] == [300, 30, 160, 165]
