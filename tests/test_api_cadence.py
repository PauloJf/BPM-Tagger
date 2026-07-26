"""Cadence-ready views: /api/run/ready and /api/run/readiness.

These answer "what can I run at X BPM" using the exact eligibility rule the run
queue uses — same octave fold, same max-stretch limit — via the shared _eligible
helper. If they ever drift from /api/run/queue the page is lying, so the fold and
limit behaviour is pinned here as well as in the queue's own tests.
"""

import os
import sqlite3

import pytest


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


def _login(client, password="s3cret"):
    client.post("/api/login", json={"password": password})
    return {"X-CSRF-Token": client.get("/api/me").get_json()["csrf_token"]}


def _seed(base_config, rows):
    """rows: (name, bpm) or (name, bpm, {col: value}) → analyzed library tracks."""
    conn = sqlite3.connect(base_config["db_path"])
    for row in rows:
        name, bpm = row[0], row[1]
        extra = row[2] if len(row) > 2 else {}
        cols = "file_path, title, artist, bpm, status"
        vals = [os.path.join(base_config["music_dir"], f"{name}.mp3"), name, "Artist", bpm, "done"]
        for k, v in extra.items():
            cols += f", {k}"
            vals.append(v)
        conn.execute(f"INSERT INTO tracks ({cols}) VALUES ({','.join('?' * len(vals))})", vals)
    conn.commit()
    conn.close()


def _titles(resp):
    return [t["title"] for t in resp.get_json()["tracks"]]


# ── /api/run/ready ───────────────────────────────────────────────────────────

def test_includes_an_in_limit_track_with_its_run_math(base_config):
    client = _app(base_config, run_stretch_limit_pct=15.0).test_client()
    _login(client)
    _seed(base_config, [("close", 150.0)])

    r = client.get("/api/run/ready?bpm=155")
    assert r.status_code == 200
    body = r.get_json()
    assert body["target"] == 155 and body["count"] == 1
    assert body["stretch_limit_pct"] == pytest.approx(15.0)
    t = body["tracks"][0]
    assert t["title"] == "close" and t["bpm"] == 150.0
    assert t["run_bpm"] == pytest.approx(150.0)
    assert t["rate"] == pytest.approx(155 / 150, abs=1e-4)


def test_excludes_an_out_of_limit_track(base_config):
    client = _app(base_config, run_stretch_limit_pct=5.0).test_client()
    _login(client)
    _seed(base_config, [("near", 150.0), ("far", 100.0)])
    assert _titles(client.get("/api/run/ready?bpm=155")) == ["near"]


def test_octave_fold_makes_a_half_tempo_track_eligible(base_config):
    """75 BPM at 150 SPM is a foot on every beat — the whole point of folding."""
    client = _app(base_config, run_octave_fold=True, run_stretch_limit_pct=10.0).test_client()
    _login(client)
    _seed(base_config, [("half", 75.0)])

    body = client.get("/api/run/ready?bpm=150").get_json()
    assert [t["title"] for t in body["tracks"]] == ["half"]
    assert body["tracks"][0]["run_bpm"] == pytest.approx(150.0)   # folded, not 75
    assert body["tracks"][0]["rate"] == pytest.approx(1.0)


def test_octave_fold_off_drops_it_again(base_config):
    client = _app(base_config, run_octave_fold=False, run_stretch_limit_pct=10.0).test_client()
    _login(client)
    _seed(base_config, [("half", 75.0)])
    assert _titles(client.get("/api/run/ready?bpm=150")) == []


def test_sorted_closest_first(base_config):
    client = _app(base_config, run_stretch_limit_pct=20.0).test_client()
    _login(client)
    _seed(base_config, [("far", 175.0), ("exact", 155.0), ("near", 160.0)])
    assert _titles(client.get("/api/run/ready?bpm=155")) == ["exact", "near", "far"]


def test_disliked_and_unanalyzed_tracks_are_out(base_config):
    """Same candidate pool as the queue — get_run_candidates does the filtering."""
    client = _app(base_config, run_stretch_limit_pct=20.0).test_client()
    _login(client)
    _seed(base_config, [("good", 155.0), ("nope", 155.0, {"disliked": 1})])
    _seed(base_config, [("pending", None)])
    assert _titles(client.get("/api/run/ready?bpm=155")) == ["good"]


def test_carries_starred_and_loudness_for_the_player(base_config):
    client = _app(base_config, run_stretch_limit_pct=20.0).test_client()
    _login(client)
    _seed(base_config, [("s", 155.0, {"starred": 1, "loudness_lufs": -8.5})])
    t = client.get("/api/run/ready?bpm=155").get_json()["tracks"][0]
    assert t["starred"] is True and t["loudness_lufs"] == pytest.approx(-8.5)


@pytest.mark.parametrize("qs", ["", "?bpm=", "?bpm=abc", "?bpm=10", "?bpm=500"])
def test_rejects_a_bad_target(base_config, qs):
    client = _app(base_config).test_client()
    _login(client)
    assert client.get(f"/api/run/ready{qs}").status_code == 400


def test_requires_login(base_config):
    assert _app(base_config).test_client().get("/api/run/ready?bpm=155").status_code == 401


def test_player_is_blocked(base_config):
    """Not in the default-deny player allowlist — players get the Run page."""
    client = _app(base_config, run_password="runner99").test_client()
    _login(client, "runner99")
    assert client.get("/api/run/ready?bpm=155").status_code == 403


# ── /api/run/readiness ───────────────────────────────────────────────────────

def test_counts_per_preset_for_library_and_playlists(base_config):
    app = _app(base_config, run_stretch_limit_pct=10.0, run_octave_fold=False,
               run_presets=[{"name": "Easy", "bpm": 155}, {"name": "Tempo", "bpm": 175}])
    client = app.test_client()
    csrf = _login(client)
    _seed(base_config, [("e1", 155.0), ("e2", 150.0), ("t1", 175.0)])

    # One of the Easy-eligible tracks also sits in a playlist.
    pid = client.post("/api/playlists", json={"source": "local", "name": "Mix"},
                      headers=csrf).get_json()["playlist"]["id"]
    client.post(f"/api/playlists/{pid}/tracks",
                json={"path": os.path.join(base_config["music_dir"], "e1.mp3")}, headers=csrf)

    body = client.get("/api/run/readiness").get_json()
    assert [p["name"] for p in body["presets"]] == ["Easy", "Tempo"]
    assert body["stretch_limit_pct"] == pytest.approx(10.0)
    # Keys are the preset BPM as a string (JSON object keys).
    assert body["library"] == {"155": 2, "175": 1}
    pl = next(p for p in body["playlists"] if p["id"] == pid)
    assert pl["counts"] == {"155": 1, "175": 0}
    assert pl["name"] == "Mix"


def test_presets_normalize_legacy_bare_numbers(base_config):
    """Older settings.json files stored presets as plain BPM numbers."""
    client = _app(base_config, run_presets=[120, 155]).test_client()
    _login(client)
    body = client.get("/api/run/readiness").get_json()
    assert body["presets"] == [{"name": "Warmup", "bpm": 120}, {"name": "Easy", "bpm": 155}]
    assert set(body["library"]) == {"120", "155"}


def test_presets_fall_back_to_the_defaults_when_unset(base_config):
    client = _app(base_config, run_presets=[]).test_client()
    _login(client)
    body = client.get("/api/run/readiness").get_json()
    assert [p["bpm"] for p in body["presets"]] == [120, 155, 165, 175]


def test_readiness_honours_octave_folding(base_config):
    client = _app(base_config, run_octave_fold=True, run_stretch_limit_pct=10.0,
                  run_presets=[{"name": "Steady", "bpm": 150}]).test_client()
    _login(client)
    _seed(base_config, [("half", 75.0)])
    assert client.get("/api/run/readiness").get_json()["library"] == {"150": 1}


def test_readiness_requires_login_and_blocks_players(base_config):
    assert _app(base_config).test_client().get("/api/run/readiness").status_code == 401
    client = _app(base_config, run_password="runner99").test_client()
    _login(client, "runner99")
    assert client.get("/api/run/readiness").status_code == 403


def test_readiness_agrees_with_the_ready_endpoint(base_config):
    """The two must not drift — both go through _eligible."""
    client = _app(base_config, run_stretch_limit_pct=12.0,
                  run_presets=[{"name": "Easy", "bpm": 158}]).test_client()
    _login(client)
    _seed(base_config, [("a", 150.0), ("b", 170.0), ("c", 100.0)])

    ready = client.get("/api/run/ready?bpm=158").get_json()["count"]
    assert client.get("/api/run/readiness").get_json()["library"]["158"] == ready
