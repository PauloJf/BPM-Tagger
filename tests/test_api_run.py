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


def test_run_queue_stretch_limit_is_the_selection_filter(client, base_config, app):
    """Max stretch alone decides eligibility: a track that can't be pulled onto the
    target within the limit is never queued (there is no separate tolerance)."""
    _login(client)
    app.extensions["state"].config["run_octave_fold"] = False   # keep the math direct
    _seed(base_config["db_path"], base_config["music_dir"], [
        ("inside",  140.0, 0),   # 150/140 → 7.1% stretch, inside the 15% limit
        ("outside", 120.0, 0),   # 150/120 → 25% stretch, beyond it
    ])
    data = client.get("/api/run/queue?bpm=150").get_json()
    assert {t["title"] for t in data["tracks"]} == {"inside"}
    assert data["stretch_limit_pct"] == 15.0
    # Every returned rate is in-limit by construction — no clamping needed.
    assert abs(data["tracks"][0]["rate"] - 150 / 140) < 1e-3


def test_run_queue_stretch_limit_boundary_is_inclusive(client, base_config, app):
    """A track sitting exactly on the limit qualifies (and one just past doesn't)."""
    _login(client)
    st = app.extensions["state"]
    st.config["run_octave_fold"] = False
    st.config["run_stretch_limit_pct"] = 25.0
    _seed(base_config["db_path"], base_config["music_dir"], [
        ("on_limit",   120.0, 0),   # 150/120 - 1 == 0.25 exactly → in
        ("past_limit", 100.0, 0),   # 150/100 - 1 == 0.50        → out
    ])
    data = client.get("/api/run/queue?bpm=150").get_json()
    assert {t["title"] for t in data["tracks"]} == {"on_limit"}
    assert data["stretch_limit_pct"] == 25.0


def test_run_queue_raising_the_limit_widens_the_pool(client, base_config, app):
    """The one slider is the whole knob — raising it admits tracks it had excluded."""
    _login(client)
    st = app.extensions["state"]
    st.config["run_octave_fold"] = False
    _seed(base_config["db_path"], base_config["music_dir"], [("far", 120.0, 0)])
    assert client.get("/api/run/queue?bpm=150").get_json()["tracks"] == []
    st.config["run_stretch_limit_pct"] = 30.0
    data = client.get("/api/run/queue?bpm=150").get_json()
    assert {t["title"] for t in data["tracks"]} == {"far"}


def test_run_queue_tolerates_a_stale_force_param(client, base_config):
    """A tab that outlived the deploy still sends ?force=1 — it's ignored, not a 400."""
    _login(client)
    _seed(base_config["db_path"], base_config["music_dir"], [("a", 150.0, 0)])
    r = client.get("/api/run/queue?bpm=150&force=1")
    assert r.status_code == 200
    data = r.get_json()
    assert {t["title"] for t in data["tracks"]} == {"a"}
    assert "forced" not in data          # the flag is gone from the response
    assert "tolerance_pct" not in data


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
    csrf = _login(client)
    _seed(base_config["db_path"], base_config["music_dir"], [
        ("a", 150.0, 0), ("b", 150.0, 0), ("c", 150.0, 0),
    ])
    exclude = [f"{base_config['music_dir']}/a.mp3"]
    data = client.post("/api/run/queue", json={"bpm": 150, "exclude": exclude},
                       headers=csrf).get_json()
    assert {t["title"] for t in data["tracks"]} == {"b", "c"}
    assert data["recycled"] is False


def test_run_queue_recycles_when_exclude_exhausts_pool(client, base_config):
    csrf = _login(client)
    _seed(base_config["db_path"], base_config["music_dir"], [
        ("a", 150.0, 0), ("b", 150.0, 0),
    ])
    exclude = [f"{base_config['music_dir']}/a.mp3", f"{base_config['music_dir']}/b.mp3"]
    data = client.post("/api/run/queue", json={"bpm": 150, "exclude": exclude},
                       headers=csrf).get_json()
    # Every match was excluded — the server recycles the full pool rather than
    # returning an empty batch.
    assert {t["title"] for t in data["tracks"]} == {"a", "b"}
    assert data["recycled"] is True


def test_run_queue_post_bad_exclude_type(client, base_config):
    csrf = _login(client)
    _seed(base_config["db_path"], base_config["music_dir"], [("a", 150.0, 0)])
    r = client.post("/api/run/queue", json={"bpm": 150, "exclude": "not-a-list"},
                    headers=csrf)
    assert r.status_code == 400


def test_run_queue_post_requires_csrf(client, base_config):
    _login(client)
    _seed(base_config["db_path"], base_config["music_dir"], [("a", 150.0, 0)])
    assert client.post("/api/run/queue", json={"bpm": 150}).status_code == 403


def test_run_queue_post_requires_target(client):
    csrf = _login(client)
    r = client.post("/api/run/queue", json={"exclude": []}, headers=csrf)
    assert r.status_code == 400


# ── run queue: playlist source (Phase 3) ───────────────────────────────────────

def _make_playlist(db_path, music_dir, name, entries):
    """entries: (source_track_id, match_status, matched_track_or_None, removed)."""
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO playlists (source, spotify_id, name, track_count) "
                 "VALUES ('spotify', ?, ?, ?)", (name, name, len(entries)))
    pid = conn.execute("SELECT id FROM playlists WHERE name = ?", (name,)).fetchone()[0]
    for i, (sid, st, track, removed) in enumerate(entries):
        mp = f"{music_dir}/{track}.mp3" if track else None
        conn.execute(
            "INSERT INTO playlist_tracks (playlist_id, source_track_id, spotify_track_id, "
            "position, title, match_status, matched_file_path, removed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, sid, sid, i, sid, st, mp, "2020-01-01T00:00:00Z" if removed else None))
    conn.commit()
    conn.close()
    return pid


def test_run_queue_playlist_scope_and_topup(client, base_config):
    _login(client)
    _seed(base_config["db_path"], base_config["music_dir"], [
        ("a", 150.0, 0), ("b", 150.0, 0), ("c", 150.0, 0),
        ("outside", 150.0, 0),           # analyzed, in the library, not in the playlist
        ("hated", 150.0, 0),             # disliked → never eligible anywhere
    ])
    conn = sqlite3.connect(base_config["db_path"])
    conn.execute("UPDATE tracks SET disliked = 1 WHERE title = 'hated'")
    conn.commit()
    conn.close()
    pid = _make_playlist(base_config["db_path"], base_config["music_dir"], "Run", [
        ("s_a", "have", "a", False),
        ("s_b", "have", "b", False),
        ("s_miss", "missing", None, False),      # not in the library → not runnable
        ("s_c", "have", "c", True),              # tombstoned → not a playlist match
        ("s_hate", "have", "hated", False),      # disliked → not runnable
    ])

    # When the playlist alone can fill the requested count, the run stays scoped
    # to it (no top-up): only its two runnable tracks, a + b.
    scoped = client.get(f"/api/run/queue?bpm=150&playlist={pid}&count=2").get_json()
    assert {t["title"] for t in scoped["tracks"]} == {"a", "b"}
    assert scoped["topped_up"] is False
    assert scoped["playlist"] == pid
    assert all(t["from_playlist"] for t in scoped["tracks"])

    # With too few playlist matches for the (default) queue size, the run tops
    # up from the whole library at the same cadence — the playlist's tracks are
    # kept, library tracks fill the rest, and disliked stays excluded.
    data = client.get(f"/api/run/queue?bpm=150&playlist={pid}").get_json()
    titles = {t["title"] for t in data["tracks"]}
    assert {"a", "b"} <= titles          # playlist matches kept
    assert "outside" in titles           # topped up from the library
    assert "hated" not in titles         # disliked never eligible
    assert data["topped_up"] is True
    fp = {t["title"]: t["from_playlist"] for t in data["tracks"]}
    assert fp["a"] is True and fp["b"] is True    # from the playlist
    assert fp["outside"] is False                 # a library top-up

    # Whole-library (no scope) still sees everything eligible.
    full = client.get("/api/run/queue?bpm=150").get_json()
    assert {"a", "b", "c", "outside"} <= {t["title"] for t in full["tracks"]}
    assert full["playlist"] is None
    assert full["topped_up"] is False


def test_run_queue_playlist_not_found_and_bad_id(client, base_config):
    _login(client)
    _seed(base_config["db_path"], base_config["music_dir"], [("a", 150.0, 0)])
    assert client.get("/api/run/queue?bpm=150&playlist=9999").status_code == 404
    assert client.get("/api/run/queue?bpm=150&playlist=abc").status_code == 400
    # "library" is the explicit whole-library sentinel, not an error.
    assert client.get("/api/run/queue?bpm=150&playlist=library").status_code == 200


def test_run_playlists_endpoint_reports_available(client, base_config):
    _login(client)
    _seed(base_config["db_path"], base_config["music_dir"],
          [("a", 150.0, 0), ("b", 150.0, 0)])
    pid = _make_playlist(base_config["db_path"], base_config["music_dir"], "Run", [
        ("s_a", "have", "a", False),
        ("s_b", "have", "b", False),
        ("s_miss", "missing", None, False),
        ("s_c", "have", "c", True),        # tombstone
    ])
    data = client.get("/api/run/playlists").get_json()
    row = next(p for p in data["playlists"] if p["id"] == pid)
    assert row["available"] == 2          # a + b (miss/tombstone excluded)
    assert row["total"] == 4
    assert row["source"] == "spotify"


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
