"""M1 SPA JSON API: login/me/track/review/settings.

Drives the endpoints the React SPA will consume through the Flask test client
with session cookie + X-CSRF-Token header, the same way the browser will.
"""

import json
import os

import pytest


def _state(client):
    return client.application.extensions["state"]


@pytest.fixture
def auth_client(client):
    """A client logged in via the JSON /api/login endpoint (returns csrf token)."""
    resp = client.post("/api/login", json={"password": "s3cret"})
    assert resp.status_code == 200
    client._csrf = resp.get_json()["csrf_token"]
    return client


def _seed_track(client, name="song.mp3", **cols):
    """Insert a track row whose file lives inside music_dir."""
    st = _state(client)
    os.makedirs(st.music_dir, exist_ok=True)
    path = os.path.join(st.music_dir, name)
    with open(path, "wb") as f:
        f.write(b"\x00")
    st.db.upsert_track(path, "1:2", cols.get("bpm"), cols.get("bpm_dr"),
                       cols.get("bpm_es"), cols.get("bpm_lb"),
                       cols.get("confidence"), cols.get("detector", "librosa"),
                       cols.get("status", "done"),
                       needs_review=cols.get("needs_review", False))
    return path


# ---------------------------------------------------------------------------
# /api/login, /api/logout, /api/me
# ---------------------------------------------------------------------------

def test_login_success_returns_csrf_token(client):
    resp = client.post("/api/login", json={"password": "s3cret"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert len(body["csrf_token"]) >= 32


def test_login_wrong_password_is_401(client):
    resp = client.post("/api/login", json={"password": "nope"})
    assert resp.status_code == 401
    assert resp.get_json()["ok"] is False


def test_login_lockout_after_max_attempts(client):
    for _ in range(5):
        r = client.post("/api/login", json={"password": "wrong"})
        assert r.status_code == 401
    r = client.post("/api/login", json={"password": "wrong"})
    assert r.status_code == 429
    assert r.get_json()["error"] == "locked_out"
    # Correct password is also refused while locked out.
    r = client.post("/api/login", json={"password": "s3cret"})
    assert r.status_code == 429


def test_me_unauthenticated(client):
    resp = client.get("/api/me")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["authenticated"] is False
    assert body["csrf_token"]           # token minted for the login POST
    assert "version" in body


def test_me_authenticated_reports_review_count(auth_client):
    _seed_track(auth_client, needs_review=True, bpm=123.0)
    resp = auth_client.get("/api/me")
    body = resp.get_json()
    assert body["authenticated"] is True
    assert body["review_count"] >= 1


def test_logout_requires_csrf(auth_client):
    assert auth_client.post("/api/logout").status_code == 403


def test_logout_clears_session(auth_client):
    resp = auth_client.post("/api/logout",
                            headers={"X-CSRF-Token": auth_client._csrf})
    assert resp.status_code == 200
    assert auth_client.get("/api/me").get_json()["authenticated"] is False


# ---------------------------------------------------------------------------
# /api/track, /api/review
# ---------------------------------------------------------------------------

def test_track_requires_login(client):
    # SPA model: protected API returns 401 JSON (not an HTML redirect).
    assert client.get("/api/track?path=/x").status_code == 401


def test_track_returns_detail(auth_client):
    path = _seed_track(auth_client, bpm=128.0, detector="essentia", confidence=0.9)
    resp = auth_client.get("/api/track", query_string={"path": path})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["track"]["bpm"] == 128.0
    assert body["track"]["detector"] == "essentia"
    assert "playback_buffer" in body


def test_track_outside_music_dir_forbidden(auth_client):
    resp = auth_client.get("/api/track", query_string={"path": "/etc/passwd"})
    assert resp.status_code == 403


def test_track_missing_is_404(auth_client):
    path = os.path.join(_state(auth_client).music_dir, "ghost.mp3")
    os.makedirs(_state(auth_client).music_dir, exist_ok=True)
    open(path, "wb").close()
    assert auth_client.get("/api/track", query_string={"path": path}).status_code == 404


# ---------------------------------------------------------------------------
# /api/tracks/paths — Play All / Shuffle queue source
# ---------------------------------------------------------------------------

def test_track_paths_requires_login(client):
    assert client.get("/api/tracks/paths").status_code == 401


def test_track_paths_lists_matching_tracks(auth_client):
    _seed_track(auth_client, name="a.mp3", bpm=120.0)
    _seed_track(auth_client, name="b.mp3", bpm=128.0, needs_review=True)
    body = auth_client.get("/api/tracks/paths").get_json()
    paths = [t["file_path"] for t in body["tracks"]]
    assert body["count"] == 2
    assert any(p.endswith("a.mp3") for p in paths)
    assert any(p.endswith("b.mp3") for p in paths)
    assert set(body["tracks"][0].keys()) >= {"file_path", "artist"}  # what the player needs


def test_track_paths_respects_review_filter(auth_client):
    _seed_track(auth_client, name="ok.mp3", bpm=120.0)
    _seed_track(auth_client, name="flag.mp3", bpm=128.0, needs_review=True)
    body = auth_client.get("/api/tracks/paths", query_string={"filter": "review"}).get_json()
    paths = [t["file_path"] for t in body["tracks"]]
    assert len(paths) == 1 and paths[0].endswith("flag.mp3")


# ---------------------------------------------------------------------------
# /api/track/trash + /api/trash (soft-delete duplicate resolution)
# ---------------------------------------------------------------------------

def _csrf(client):
    return {"X-CSRF-Token": client._csrf}


def test_trash_moves_file_marks_deleted_and_reports(auth_client):
    path = _seed_track(auth_client, name="dupe.mp3", bpm=120.0)
    r = auth_client.post("/api/track/trash", json={"file_path": path}, headers=_csrf(auth_client))
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert not os.path.exists(path)                                   # moved out of the library
    assert _state(auth_client).db.get_track(path)["status"] == "deleted"
    info = auth_client.get("/api/trash").get_json()
    assert info["count"] == 1 and info["bytes"] >= 1


def test_trash_refuses_locked_track(auth_client):
    path = _seed_track(auth_client, name="keep.mp3", bpm=120.0)
    _state(auth_client).db.lock_track(path, 120.0)
    r = auth_client.post("/api/track/trash", json={"file_path": path}, headers=_csrf(auth_client))
    assert r.status_code == 400 and os.path.exists(path)              # locked → protected


def test_trash_requires_csrf(auth_client):
    path = _seed_track(auth_client, name="x.mp3")
    assert auth_client.post("/api/track/trash", json={"file_path": path}).status_code == 403


def test_trash_outside_music_dir_forbidden(auth_client):
    assert auth_client.post("/api/track/trash", json={"file_path": "/etc/passwd"},
                            headers=_csrf(auth_client)).status_code == 403


def test_trash_purge_empties(auth_client):
    path = _seed_track(auth_client, name="dupe.mp3", bpm=120.0)
    auth_client.post("/api/track/trash", json={"file_path": path}, headers=_csrf(auth_client))
    r = auth_client.post("/api/trash/purge", headers=_csrf(auth_client))
    assert r.status_code == 200 and r.get_json()["removed"] == 1
    assert auth_client.get("/api/trash").get_json()["count"] == 0


# ---------------------------------------------------------------------------
# /api/isrc/lookup (Find ISRC) — auth + empty-query only (no external calls)
# ---------------------------------------------------------------------------

def test_isrc_lookup_requires_login(client):
    assert client.get("/api/isrc/lookup").status_code == 401


def test_isrc_lookup_empty_query_returns_no_candidates(auth_client):
    # No artist/title → returns immediately without hitting Spotify/MusicBrainz.
    body = auth_client.get("/api/isrc/lookup").get_json()
    assert body["candidates"] == [] and body["spotify_search_url"] == ""


def test_track_isrc_sets_isrc(auth_client):
    path = _seed_track(auth_client, name="song.mp3", bpm=120.0)
    r = auth_client.post("/api/track/isrc", json={"file_path": path, "isrc": "usum71234567"},
                         headers=_csrf(auth_client))
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert _state(auth_client).db.get_track(path)["isrc"] == "usum71234567"


def test_track_isrc_requires_csrf(auth_client):
    path = _seed_track(auth_client, name="song.mp3")
    assert auth_client.post("/api/track/isrc", json={"file_path": path, "isrc": "x"}).status_code == 403


def test_isrc_fill_status_idle(auth_client):
    body = auth_client.get("/api/isrc/fill/status").get_json()
    assert body["running"] is False and body["unresolved"] == []


def test_isrc_fill_status_requires_login(client):
    assert client.get("/api/isrc/fill/status").status_code == 401


def test_tracks_search_matches_indexed_metadata(auth_client):
    # Filename has no artist/title; search must still find it via indexed tags.
    path = _seed_track(auth_client, name="01 - track.mp3", bpm=120.0)
    _state(auth_client).db.update_track_metadata(path, path, {
        "title": "Blinding Lights", "artist": "The Weeknd", "album": "After Hours",
        "album_artist": "The Weeknd", "track_no": 1, "disc_no": 1, "year": 2020,
        "isrc": "", "norm_title": "blinding lights", "norm_artist": "the weeknd",
    }, "1:2")
    body = auth_client.get("/api/tracks", query_string={"q": "Weeknd"}).get_json()
    assert any(t["file_path"] == path for t in body["tracks"])


def test_artist_lists_tracks_and_stats(auth_client):
    p1 = _seed_track(auth_client, name="a.mp3", bpm=120.0)
    p2 = _seed_track(auth_client, name="b.mp3", bpm=128.0)
    st = _state(auth_client)
    for p, bpm, alb in ((p1, 120.0, "After Hours"), (p2, 128.0, "Dawn FM")):
        st.db.update_track_metadata(p, p, {
            "title": "T", "artist": "The Weeknd", "album": alb, "album_artist": "The Weeknd",
            "track_no": 1, "disc_no": 1, "year": 2020, "isrc": "",
            "norm_title": "t", "norm_artist": "the weeknd",
        }, "1:2")
    body = auth_client.get("/api/artist", query_string={"name": "The Weeknd"}).get_json()
    assert body["stats"]["tracks"] == 2 and body["stats"]["albums"] == 2
    assert {t["file_path"] for t in body["tracks"]} == {p1, p2}


def test_artist_requires_login(client):
    assert client.get("/api/artist", query_string={"name": "X"}).status_code == 401


def test_track_review_prev_next(auth_client):
    a = _seed_track(auth_client, "a.mp3", needs_review=True, bpm=100.0)
    _seed_track(auth_client, "b.mp3", needs_review=True, bpm=101.0)
    resp = auth_client.get("/api/track", query_string={"path": a, "back": "review"})
    body = resp.get_json()
    assert body["queue_total"] == 2
    assert body["queue_pos"] == 1
    assert body["prev_path"] is None
    assert body["next_path"] is not None


def test_review_lists_suspicious(auth_client):
    _seed_track(auth_client, "r.mp3", needs_review=True, bpm=99.0)
    resp = auth_client.get("/api/review")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["total"] >= 1
    assert body["page"] == 1
    assert any(t["file_path"].endswith("r.mp3") for t in body["tracks"])


# ---------------------------------------------------------------------------
# /api/settings (GET + JSON POST)
# ---------------------------------------------------------------------------

def test_settings_get_masks_secrets(auth_client):
    resp = auth_client.get("/api/settings")
    assert resp.status_code == 200
    s = resp.get_json()["settings"]
    # ui_password is set in the test config → masked, never returned in clear.
    assert s["ui_password"] == "********"
    assert s["ui_password"] != "s3cret"
    assert s["ui_secret_key"] == "********"


def test_settings_get_serializes_extensions_set(auth_client):
    _state(auth_client).config["extensions"] = {".mp3", ".flac"}
    s = auth_client.get("/api/settings").get_json()["settings"]
    assert isinstance(s["extensions"], list)
    assert sorted(s["extensions"]) == [".flac", ".mp3"]


def test_settings_scan_json_persists_and_updates_state(auth_client):
    resp = auth_client.post("/api/settings/scan",
                            json={"workers": 3, "write_tags": True,
                                  "review_confidence_threshold": 0.6,
                                  "bpm_min": 70, "bpm_max": 180},
                            headers={"X-CSRF-Token": auth_client._csrf})
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    st = _state(auth_client)
    assert st.conf_threshold == 0.6
    assert st.bpm_min == 70.0
    assert st.write_tags is True
    assert st.config["workers"] == 3
    with open(st.settings_path) as f:
        assert json.load(f)["workers"] == 3


def test_settings_scan_json_requires_csrf(auth_client):
    assert auth_client.post("/api/settings/scan", json={"workers": 2}).status_code == 403


def test_settings_mode_rejects_invalid(auth_client):
    resp = auth_client.post("/api/settings/mode", json={"mode": "bogus"},
                            headers={"X-CSRF-Token": auth_client._csrf})
    assert resp.status_code == 400
    assert _state(auth_client).config.get("mode") != "bogus"


def test_settings_playback_clamps(auth_client):
    auth_client.post("/api/settings/playback", json={"playback_buffer": 99},
                     headers={"X-CSRF-Token": auth_client._csrf})
    assert _state(auth_client).config["playback_buffer"] == 30.0


def test_settings_navidrome_keeps_password_when_masked(auth_client):
    st = _state(auth_client)
    st.config["navidrome_pass"] = "secret-pw"
    auth_client.post("/api/settings/navidrome",
                     json={"navidrome_url": "http://nd", "navidrome_user": "u",
                           "navidrome_pass": "********"},
                     headers={"X-CSRF-Token": auth_client._csrf})
    assert st.config["navidrome_pass"] == "secret-pw"   # unchanged
    assert st.config["navidrome_url"] == "http://nd"


def test_settings_password_change_round_trip(auth_client):
    resp = auth_client.post("/api/settings/password",
                            json={"current_password": "s3cret",
                                  "new_password": "brand-new",
                                  "confirm_password": "brand-new"},
                            headers={"X-CSRF-Token": auth_client._csrf})
    assert resp.status_code == 200
    assert auth_client.application.config["UI_PASSWORD"] == "brand-new"


def test_settings_password_rejects_wrong_current(auth_client):
    resp = auth_client.post("/api/settings/password",
                            json={"current_password": "WRONG",
                                  "new_password": "x", "confirm_password": "x"},
                            headers={"X-CSRF-Token": auth_client._csrf})
    assert resp.status_code == 400
