"""Phase-4 Local playlists + "Add to playlist".

Local is a third playlist ``source`` on the same tables. These exercise the DB
authoring layer (create / add / remove, idempotence) and assert that the existing
source-agnostic machinery — coverage counts, m3u rows, and the run-candidate
queries — picks up a Local playlist's tracks unchanged. A second group covers the
web API: source-aware add/remove, the local-only guards, grabber-independence, and
the player role's lack of management access.
"""

import os
import sqlite3

import pytest

from bpm_tagger.db import BPMDatabase


@pytest.fixture
def db(tmp_path):
    return BPMDatabase(str(tmp_path / "bpm.db"))


def _seed_track(db, path, title="Song", artist="Artist", bpm=150.0, status="done"):
    """Insert a minimal library track (as the scanner would) so it can be added
    to a Local playlist."""
    with db._connect() as conn:
        conn.execute(
            "INSERT INTO tracks (file_path, title, artist, album, album_artist, "
            "duration_ms, norm_title, norm_artist, bpm, status) "
            "VALUES (?, ?, ?, 'Alb', ?, 200000, ?, ?, ?, ?)",
            (path, title, artist, artist, title.lower(), artist.lower(), bpm, status))
        conn.commit()
    return path


def _rows(db, pid):
    return {t["title"]: t for t in db.get_playlist_tracks(pid)}


# ── DB authoring layer ───────────────────────────────────────────────────────

def test_create_local_playlist(db):
    pid = db.add_local_playlist("My Run Mix")
    pl = db.get_playlist(pid)
    assert pl["source"] == "local"
    assert pl["spotify_id"] is None and pl["navidrome_id"] is None
    assert pl["name"] == "My Run Mix" and pl["track_count"] == 0


def test_add_track_is_directly_have(db):
    path = _seed_track(db, "/music/a.mp3", "A")
    pid = db.add_local_playlist("PL")
    assert db.add_track_to_local_playlist(pid, path) is True

    row = _rows(db, pid)["A"]
    assert row["derived_status"] == "have"
    assert row["match_status"] == "have" and row["matched_file_path"] == path
    # Membership flags are inert for Local (no sync): not "new", never tombstoned.
    assert row["is_new"] == 0 and row["removed_at"] is None
    assert db.get_playlist(pid)["track_count"] == 1


def test_add_track_is_idempotent(db):
    path = _seed_track(db, "/music/a.mp3", "A")
    pid = db.add_local_playlist("PL")
    assert db.add_track_to_local_playlist(pid, path) is True
    assert db.add_track_to_local_playlist(pid, path) is False   # already present
    assert len(db.get_playlist_track_rows(pid)) == 1
    assert db.get_playlist(pid)["track_count"] == 1


def test_add_unknown_track_raises(db):
    pid = db.add_local_playlist("PL")
    with pytest.raises(ValueError):
        db.add_track_to_local_playlist(pid, "/music/nope.mp3")


def test_add_deleted_track_raises(db):
    path = _seed_track(db, "/music/gone.mp3", "Gone", status="deleted")
    pid = db.add_local_playlist("PL")
    with pytest.raises(ValueError):
        db.add_track_to_local_playlist(pid, path)


def test_remove_track_hard_deletes(db):
    a = _seed_track(db, "/music/a.mp3", "A")
    b = _seed_track(db, "/music/b.mp3", "B")
    pid = db.add_local_playlist("PL")
    db.add_track_to_local_playlist(pid, a)
    db.add_track_to_local_playlist(pid, b)

    pt_id = _rows(db, pid)["A"]["id"]
    db.remove_playlist_track(pt_id)
    rows = _rows(db, pid)
    assert set(rows) == {"B"}                       # hard gone, no tombstone
    assert db.get_playlist(pid)["track_count"] == 1


def test_delete_local_playlist_cascades(db):
    path = _seed_track(db, "/music/a.mp3", "A")
    pid = db.add_local_playlist("PL")
    db.add_track_to_local_playlist(pid, path)
    db.delete_playlist(pid)
    assert db.get_playlist(pid) is None
    assert db.get_playlist_track_rows(pid) == []


# ── existing machinery, unchanged (verify, don't rewrite) ────────────────────

def test_local_flows_through_shared_machinery(db):
    path = _seed_track(db, "/music/a.mp3", "A", bpm=150.0)
    pid = db.add_local_playlist("PL")
    db.add_track_to_local_playlist(pid, path)

    # Coverage counts (list_playlists): a Local 'have' row counts as have, never
    # queued (spotify_track_id is NULL).
    pl = next(p for p in db.list_playlists() if p["id"] == pid)
    assert pl["have_count"] == 1 and pl["missing_count"] == 0
    assert pl["queued_count"] == 0 and pl["indexed_count"] == 1

    # m3u export source (live rows carry matched_file_path).
    live = db.get_playlist_track_rows(pid)
    assert [r["matched_file_path"] for r in live] == [path]

    # Run-mode source: the track is a runnable candidate for the playlist scope.
    cands = db.get_run_candidates(pid)
    assert [c["file_path"] for c in cands] == [path]
    assert db.count_run_candidates(pid) == 1


def test_disliked_local_track_is_not_runnable(db):
    path = _seed_track(db, "/music/a.mp3", "A")
    with db._connect() as conn:
        conn.execute("UPDATE tracks SET disliked = 1 WHERE file_path = ?", (path,))
        conn.commit()
    pid = db.add_local_playlist("PL")
    db.add_track_to_local_playlist(pid, path)
    # Still 'have' in coverage, but excluded from run candidates (shared filter).
    assert next(p for p in db.list_playlists() if p["id"] == pid)["have_count"] == 1
    assert db.count_run_candidates(pid) == 0


# ── web API ──────────────────────────────────────────────────────────────────

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


def _login(client, password):
    resp = client.post("/api/login", json={"password": password})
    csrf = None
    if resp.status_code == 200:
        csrf = {"X-CSRF-Token": client.get("/api/me").get_json()["csrf_token"]}
    return resp, csrf


def _seed_api_track(base_config, name="song", bpm=150.0):
    path = os.path.join(base_config["music_dir"], f"{name}.mp3")
    conn = sqlite3.connect(base_config["db_path"])
    conn.execute(
        "INSERT INTO tracks (file_path, title, artist, bpm, status) "
        "VALUES (?, ?, 'Artist', ?, 'done')", (path, name, bpm))
    conn.commit()
    conn.close()
    return path


def test_api_create_local_playlist_grabber_off(base_config):
    """Local creation needs neither the grabber nor Spotify."""
    client = _app(base_config).test_client()
    _, csrf = _login(client, "s3cret")
    r = client.post("/api/playlists", json={"source": "local", "name": "Mix"}, headers=csrf)
    assert r.status_code == 200
    pl = r.get_json()["playlist"]
    assert pl["source"] == "local" and pl["name"] == "Mix"


def test_api_create_local_requires_name(base_config):
    client = _app(base_config).test_client()
    _, csrf = _login(client, "s3cret")
    r = client.post("/api/playlists", json={"source": "local", "name": "  "}, headers=csrf)
    assert r.status_code == 400


def test_api_add_and_remove_track(base_config):
    app = _app(base_config)
    client = app.test_client()
    _, csrf = _login(client, "s3cret")
    path = _seed_api_track(base_config)

    pid = client.post("/api/playlists", json={"source": "local", "name": "Mix"},
                      headers=csrf).get_json()["playlist"]["id"]

    add = client.post(f"/api/playlists/{pid}/tracks", json={"path": path}, headers=csrf)
    assert add.status_code == 200 and add.get_json()["added"] is True

    # The row is now visible via the detail endpoint; grab its id to remove it.
    tracks = client.get(f"/api/playlists/{pid}/tracks").get_json()["tracks"]
    assert len(tracks) == 1 and tracks[0]["derived_status"] == "have"
    pt_id = tracks[0]["id"]

    def _have():
        pls = client.get("/api/playlists").get_json()["playlists"]
        return next(p for p in pls if p["id"] == pid)["have_count"]
    assert _have() == 1

    rm = client.delete(f"/api/playlists/{pid}/tracks/{pt_id}", headers=csrf)
    assert rm.status_code == 200
    assert _have() == 0


def test_api_add_track_rejects_missing_file(base_config):
    app = _app(base_config)
    client = app.test_client()
    _, csrf = _login(client, "s3cret")
    pid = client.post("/api/playlists", json={"source": "local", "name": "Mix"},
                      headers=csrf).get_json()["playlist"]["id"]
    # Path is under music_dir (passes the sandbox check) but not a known track.
    ghost = os.path.join(base_config["music_dir"], "ghost.mp3")
    r = client.post(f"/api/playlists/{pid}/tracks", json={"path": ghost}, headers=csrf)
    assert r.status_code == 404


def test_api_add_track_rejects_path_outside_music_dir(base_config):
    app = _app(base_config)
    client = app.test_client()
    _, csrf = _login(client, "s3cret")
    pid = client.post("/api/playlists", json={"source": "local", "name": "Mix"},
                      headers=csrf).get_json()["playlist"]["id"]
    r = client.post(f"/api/playlists/{pid}/tracks",
                    json={"path": "/etc/passwd"}, headers=csrf)
    assert r.status_code == 403


def test_api_add_track_rejects_non_local(base_config):
    app = _app(base_config)
    client = app.test_client()
    _, csrf = _login(client, "s3cret")
    path = _seed_api_track(base_config)
    # A non-local playlist (created directly) must reject manual adds.
    from bpm_tagger.web.state import state
    with app.app_context():
        pid = state().db.add_playlist("SPX", "Spotify PL")
    r = client.post(f"/api/playlists/{pid}/tracks", json={"path": path}, headers=csrf)
    assert r.status_code == 400


def test_api_sync_rejects_local(base_config):
    app = _app(base_config)
    client = app.test_client()
    _, csrf = _login(client, "s3cret")
    pid = client.post("/api/playlists", json={"source": "local", "name": "Mix"},
                      headers=csrf).get_json()["playlist"]["id"]
    r = client.post(f"/api/playlists/{pid}/sync", headers=csrf)
    assert r.status_code == 400


def test_player_cannot_manage_local_playlists(base_config):
    app = _app(base_config, run_password="runner99")
    client = app.test_client()
    _, csrf = _login(client, "runner99")
    # Management endpoints are outside the player allowlist → 403.
    assert client.post("/api/playlists", json={"source": "local", "name": "Mix"},
                       headers=csrf).status_code == 403
    assert client.post("/api/playlists/1/tracks", json={"path": "/x"},
                       headers=csrf).status_code == 403
    assert client.post("/api/playlists/1/import", json={"from_playlist_id": 2},
                       headers=csrf).status_code == 403


# ── bulk import (copy a playlist's library tracks into a Local playlist) ───────

def _src_spotify(db, tracks):
    """Build a Spotify-source playlist with the given [(sid, title, match_status,
    matched_file_path)] rows via the normal sync path."""
    db.add_playlist("SRC", "Source")
    sid = db.get_playlist_by_spotify_id("SRC")["id"]
    db.sync_playlist_tracks(sid, [
        {"source_track_id": s, "title": t, "match_status": ms, "matched_file_path": mp}
        for (s, t, ms, mp) in tracks])
    return sid


def test_import_copies_only_have_rows(db):
    a = _seed_track(db, "/music/a.mp3", "A")
    b = _seed_track(db, "/music/b.mp3", "B")
    src = _src_spotify(db, [
        ("t1", "A", "have", a),        # library-backed → copied
        ("t2", "Ghost", "missing", None),  # no file → skipped
        ("t3", "B", "have", b),        # library-backed → copied
    ])
    dest = db.add_local_playlist("Dest")

    counts = db.import_playlist_tracks(dest, src)
    assert counts == {"added": 2, "already_present": 0, "skipped_missing": 1}
    assert set(_rows(db, dest)) == {"A", "B"}
    # Copied rows are directly 'have' and carry the library file path.
    row = _rows(db, dest)["A"]
    assert row["derived_status"] == "have" and row["matched_file_path"] == a
    assert db.get_playlist(dest)["track_count"] == 2


def test_import_is_idempotent(db):
    a = _seed_track(db, "/music/a.mp3", "A")
    src = _src_spotify(db, [("t1", "A", "have", a)])
    dest = db.add_local_playlist("Dest")

    assert db.import_playlist_tracks(dest, src)["added"] == 1
    # Re-import: nothing new, the one row is already present.
    assert db.import_playlist_tracks(dest, src) == {
        "added": 0, "already_present": 1, "skipped_missing": 0}
    assert len(db.get_playlist_track_rows(dest)) == 1


def test_import_skips_row_matched_to_deleted_track(db):
    gone = _seed_track(db, "/music/gone.mp3", "Gone", status="deleted")
    src = _src_spotify(db, [("t1", "Gone", "have", gone)])
    dest = db.add_local_playlist("Dest")
    # match_status says 'have', but the library track is deleted → nothing to add.
    assert db.import_playlist_tracks(dest, src) == {
        "added": 0, "already_present": 0, "skipped_missing": 1}


def test_import_ignores_tombstoned_source_rows(db):
    a = _seed_track(db, "/music/a.mp3", "A")
    src = _src_spotify(db, [("t1", "A", "have", a)])
    db.sync_playlist_tracks(src, [])          # t1 now absent → tombstoned
    dest = db.add_local_playlist("Dest")
    assert db.import_playlist_tracks(dest, src)["added"] == 0


def test_import_local_into_local_merges(db):
    a = _seed_track(db, "/music/a.mp3", "A")
    b = _seed_track(db, "/music/b.mp3", "B")
    src = db.add_local_playlist("Src")
    db.add_track_to_local_playlist(src, a)
    db.add_track_to_local_playlist(src, b)
    dest = db.add_local_playlist("Dest")
    db.add_track_to_local_playlist(dest, a)   # already has A

    assert db.import_playlist_tracks(dest, src) == {
        "added": 1, "already_present": 1, "skipped_missing": 0}
    assert set(_rows(db, dest)) == {"A", "B"}


def test_api_import_rejects_non_local_dest(base_config):
    app = _app(base_config)
    client = app.test_client()
    _, csrf = _login(client, "s3cret")
    from bpm_tagger.web.state import state
    with app.app_context():
        src = state().db.add_local_playlist("Src")
        dest = state().db.add_playlist("SPX", "Spotify PL")  # non-local dest
    r = client.post(f"/api/playlists/{dest}/import",
                    json={"from_playlist_id": src}, headers=csrf)
    assert r.status_code == 400


def test_api_import_rejects_self(base_config):
    app = _app(base_config)
    client = app.test_client()
    _, csrf = _login(client, "s3cret")
    pid = client.post("/api/playlists", json={"source": "local", "name": "Mix"},
                      headers=csrf).get_json()["playlist"]["id"]
    r = client.post(f"/api/playlists/{pid}/import",
                    json={"from_playlist_id": pid}, headers=csrf)
    assert r.status_code == 400


def test_api_import_missing_source(base_config):
    app = _app(base_config)
    client = app.test_client()
    _, csrf = _login(client, "s3cret")
    pid = client.post("/api/playlists", json={"source": "local", "name": "Mix"},
                      headers=csrf).get_json()["playlist"]["id"]
    r = client.post(f"/api/playlists/{pid}/import",
                    json={"from_playlist_id": 9999}, headers=csrf)
    assert r.status_code == 404


def test_api_import_returns_counts(base_config):
    app = _app(base_config)
    client = app.test_client()
    _, csrf = _login(client, "s3cret")
    path = _seed_api_track(base_config)
    from bpm_tagger.web.state import state
    with app.app_context():
        db = state().db
        src = _src_spotify(db, [("t1", "song", "have", path),
                                ("t2", "Ghost", "missing", None)])
    dest = client.post("/api/playlists", json={"source": "local", "name": "Dest"},
                       headers=csrf).get_json()["playlist"]["id"]
    r = client.post(f"/api/playlists/{dest}/import",
                    json={"from_playlist_id": src}, headers=csrf)
    assert r.status_code == 200
    assert r.get_json()["counts"] == {
        "added": 1, "already_present": 0, "skipped_missing": 1}


# ── library "not in a playlist" filter + stats count ──────────────────────────

def test_unplaylisted_filter_and_count(db):
    a = _seed_track(db, "/music/a.mp3", "A")   # will be in a Local playlist
    b = _seed_track(db, "/music/b.mp3", "B")   # matched by a remote playlist
    _seed_track(db, "/music/c.mp3", "C")       # in nothing → unplaylisted
    _seed_track(db, "/music/x.mp3", "X", status="deleted")  # excluded (deleted)

    local = db.add_local_playlist("PL")
    db.add_track_to_local_playlist(local, a)
    _src_spotify(db, [("t1", "B", "have", b)])

    titles = {r["title"] for r in db.get_tracks_page("", 50, 0, filter="unplaylisted")[0]}
    assert titles == {"C"}
    assert db.get_stats()["unplaylisted"] == 1


def test_unplaylisted_ignores_tombstoned_coverage(db):
    d = _seed_track(db, "/music/d.mp3", "D")
    src = _src_spotify(db, [("t1", "D", "have", d)])
    db.sync_playlist_tracks(src, [])           # D's coverage row is tombstoned
    # A tombstone is not live coverage → D counts as unplaylisted again.
    titles = {r["title"] for r in db.get_tracks_page("", 50, 0, filter="unplaylisted")[0]}
    assert "D" in titles
    assert db.get_stats()["unplaylisted"] == 1
