"""Phase-2 Navidrome playlists: sync/resolve module + source-aware playlists API,
with a fake Subsonic layer (no network)."""

import pytest

from bpm_tagger.bpm.tags import get_file_hash
from bpm_tagger.db import BPMDatabase
from bpm_tagger.grabber import matching as m
from bpm_tagger.integrations import navidrome_playlists as np
from bpm_tagger.web.app import create_app

CONFIG = {"navidrome_url": "http://nd", "navidrome_user": "admin", "navidrome_pass": "pw"}


def _seed_library(db, tmp_path):
    """One analyzed library track that a playlist song can match as 'have'."""
    lib = tmp_path / "music" / "bl.mp3"
    lib.parent.mkdir(parents=True, exist_ok=True)
    lib.write_bytes(b"\x00")
    h = get_file_hash(str(lib))
    db.upsert_track(str(lib), h, 171.0, None, None, 171.0, 0.9, "librosa", "done")
    db.update_track_tags(str(lib), {
        "title": "Blinding Lights", "artist": "The Weeknd", "album": "After Hours",
        "album_artist": "The Weeknd", "track_no": 1, "disc_no": 1, "year": 2020,
        "isrc": "", "duration_ms": 200000,
        "norm_title": m.normalize_title("Blinding Lights"),
        "norm_artist": m.normalize_artist("The Weeknd"),
    }, h)
    return str(lib)


def _song(sid, title, artist, **kw):
    return {"id": sid, "title": title, "artist": artist, "album": kw.get("album", ""),
            "albumArtist": artist, "duration": kw.get("duration", 200), "track": 1,
            "discNumber": 1, "year": 2020, "coverArt": "", "path": kw.get("path", "")}


@pytest.fixture
def db(tmp_path):
    return BPMDatabase(str(tmp_path / "bpm.db"))


# ── module: list / sync / resolve ────────────────────────────────────────────

def test_list_navidrome_playlists(monkeypatch):
    monkeypatch.setattr(np, "get_playlists", lambda u, us, p: [
        {"id": "p1", "name": "Run", "songCount": 3, "coverArt": "c1"},
        {"id": 2, "name": "Chill", "songCount": 0},
        {"name": "no-id"},   # skipped
    ])
    out = np.list_navidrome_playlists(CONFIG)
    assert out == [
        {"navidrome_id": "p1", "name": "Run", "track_count": 3, "image_url": "c1"},
        {"navidrome_id": "2", "name": "Chill", "track_count": 0, "image_url": ""},
    ]


def test_sync_classifies_have_and_missing(db, tmp_path, monkeypatch):
    lib = _seed_library(db, tmp_path)
    pid = db.add_navidrome_playlist("p1", "Run")
    monkeypatch.setattr(np, "get_playlist", lambda u, us, p, i: {"name": "Run", "entry": [
        _song("s1", "Blinding Lights", "The Weeknd", album="After Hours"),
        _song("s2", "Totally Missing", "Nobody At All"),
    ]})
    pl = np.sync_navidrome_playlist(db, CONFIG, pid)
    assert pl["source"] == "navidrome" and pl["track_count"] == 2

    tracks = {t["title"]: t for t in db.get_playlist_tracks(pid)}
    assert tracks["Blinding Lights"]["derived_status"] == "have"
    assert tracks["Blinding Lights"]["matched_file_path"] == lib
    assert tracks["Blinding Lights"]["source_track_id"] == "s1"
    assert tracks["Totally Missing"]["derived_status"] == "missing"

    row = next(p for p in db.list_playlists() if p["id"] == pid)
    assert row["have_count"] == 1 and row["missing_count"] == 1
    assert row["new_count"] == 2


def test_sync_tombstones_removed_track(db, tmp_path, monkeypatch):
    _seed_library(db, tmp_path)
    pid = db.add_navidrome_playlist("p1", "Run")
    full = [_song("s1", "Blinding Lights", "The Weeknd", album="After Hours"),
            _song("s2", "Totally Missing", "Nobody At All")]
    monkeypatch.setattr(np, "get_playlist", lambda u, us, p, i: {"name": "Run", "entry": full})
    np.sync_navidrome_playlist(db, CONFIG, pid)
    db.mark_playlist_seen(pid)

    monkeypatch.setattr(np, "get_playlist", lambda u, us, p, i: {"name": "Run", "entry": full[:1]})
    np.sync_navidrome_playlist(db, CONFIG, pid)

    tracks = {t["title"]: t for t in db.get_playlist_tracks(pid)}
    assert tracks["Totally Missing"]["derived_status"] == "removed"
    live = {r["title"] for r in db.get_playlist_track_rows(pid)}
    assert live == {"Blinding Lights"}


def test_sync_rejects_non_navidrome_playlist(db):
    pid = db.add_playlist("PLspot", "Spotify one")
    with pytest.raises(ValueError):
        np.sync_navidrome_playlist(db, CONFIG, pid)


# ── API: source-aware add/sync + browse ──────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    import os
    config = {
        "db_path": str(tmp_path / "bpm.db"),
        "music_dir": str(tmp_path / "music"),
        "ui_password": "s3cret",
        "ui_secret_key": "k",
        **CONFIG,
    }
    os.makedirs(config["music_dir"], exist_ok=True)
    app = create_app(config)
    st = app.extensions["state"]
    _seed_library(st.db, tmp_path)
    monkeypatch.setattr(np, "get_playlists", lambda u, us, p: [
        {"id": "p1", "name": "Run", "songCount": 2, "coverArt": ""}])
    monkeypatch.setattr(np, "get_playlist", lambda u, us, p, i: {"name": "Run", "entry": [
        _song("s1", "Blinding Lights", "The Weeknd", album="After Hours"),
        _song("s2", "Totally Missing", "Nobody At All")]})
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/api/login", json={"password": "s3cret"})
    c._csrf = c.get("/api/me").get_json()["csrf_token"]
    return c, st


def test_api_browse_navidrome_playlists(client):
    c, _ = client
    body = c.get("/api/navidrome/playlists").get_json()
    assert body["playlists"][0]["navidrome_id"] == "p1"
    assert body["playlists"][0]["watched"] is False


def test_api_add_and_sync_navidrome_playlist(client):
    c, st = client
    r = c.post("/api/playlists", json={"source": "navidrome", "navidrome_id": "p1", "name": "Run"},
               headers={"X-CSRF-Token": c._csrf})
    assert r.status_code == 200
    pl = r.get_json()["playlist"]
    assert pl["source"] == "navidrome" and pl["navidrome_id"] == "p1"

    listing = c.get("/api/playlists").get_json()["playlists"]
    assert any(p["navidrome_id"] == "p1" and p["source"] == "navidrome" for p in listing)

    # It now shows as watched in the browse view.
    browse = c.get("/api/navidrome/playlists").get_json()["playlists"]
    assert browse[0]["watched"] is True

    # Re-sync via the source-aware endpoint (no grabber needed).
    r2 = c.post(f"/api/playlists/{pl['id']}/sync", headers={"X-CSRF-Token": c._csrf})
    assert r2.status_code == 200

    tracks = c.get(f"/api/playlists/{pl['id']}/tracks?status=have").get_json()["tracks"]
    assert [t["title"] for t in tracks] == ["Blinding Lights"]


def test_api_add_navidrome_requires_config(tmp_path):
    import os
    config = {"db_path": str(tmp_path / "b.db"), "music_dir": str(tmp_path / "m"),
              "ui_password": "s3cret", "ui_secret_key": "k"}  # no navidrome creds
    os.makedirs(config["music_dir"], exist_ok=True)
    app = create_app(config)
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/api/login", json={"password": "s3cret"})
    csrf = c.get("/api/me").get_json()["csrf_token"]
    r = c.post("/api/playlists", json={"source": "navidrome", "navidrome_id": "p1"},
               headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400
    assert r.get_json()["error"] == "navidrome_not_configured"
