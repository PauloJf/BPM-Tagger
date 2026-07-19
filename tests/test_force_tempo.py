"""Phase 5 §7 — "play everything, force tempo": tolerance drop + rate clamp."""

import sqlite3


def _login(client):
    assert client.post("/api/login", json={"password": "s3cret"}).status_code == 200


def _seed(db_path, music_dir, rows):
    conn = sqlite3.connect(db_path)
    for name, bpm in rows:
        conn.execute(
            "INSERT INTO tracks (file_path, title, artist, bpm, status) "
            "VALUES (?, ?, 'Artist', ?, 'done')", (f"{music_dir}/{name}.mp3", name, bpm))
    conn.commit()
    conn.close()


def test_force_includes_out_of_tolerance_and_clamps(client, app, base_config):
    _login(client)
    # Octave fold off so a far BPM stays far (fold would otherwise pull it in range).
    app.extensions["state"].config["run_octave_fold"] = False
    _seed(base_config["db_path"], base_config["music_dir"], [("near", 150.0), ("far", 50.0)])

    # Without force: only the in-tolerance track qualifies.
    data = client.get("/api/run/queue?bpm=150").get_json()
    assert data["forced"] is False
    assert {t["title"] for t in data["tracks"]} == {"near"}

    # With force: both qualify; the far one is clamped to the max rate (2.0) and flagged.
    data = client.get("/api/run/queue?bpm=150&force=1").get_json()
    assert data["forced"] is True
    by_title = {t["title"]: t for t in data["tracks"]}
    assert set(by_title) == {"near", "far"}
    assert by_title["near"]["clamped"] is False
    assert by_title["far"]["clamped"] is True
    assert by_title["far"]["rate"] == 2.0        # 150/50 = 3.0 → clamped to 2.0


def test_force_defaults_from_setting(client, app, base_config):
    _login(client)
    app.extensions["state"].config["run_octave_fold"] = False
    app.extensions["state"].config["run_force_tempo"] = True
    _seed(base_config["db_path"], base_config["music_dir"], [("far", 50.0)])
    # No explicit force flag → falls back to the run_force_tempo setting (on).
    data = client.get("/api/run/queue?bpm=150").get_json()
    assert data["forced"] is True
    assert {t["title"] for t in data["tracks"]} == {"far"}
