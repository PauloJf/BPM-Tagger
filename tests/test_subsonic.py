"""Optional Subsonic API (Phase 1) — docs/plans/subsonic-api.md."""

import hashlib
import os
import sqlite3
import xml.etree.ElementTree as ET

import numpy as np
import pytest
import soundfile as sf

from bpm_tagger.config import build_config
from bpm_tagger.web.subsonic import ids

NS = "{http://subsonic.org/restapi}"


def _app(base_config, **over):
    from bpm_tagger.web.app import create_app

    cfg = build_config()
    cfg.update({
        "db_path": base_config["db_path"],
        "music_dir": base_config["music_dir"],
        "ui_password": "s3cret",
        "ui_secret_key": "unit-test-secret-key",
        "write_tags": False,
        "subsonic_enabled": True,
    })
    cfg.update(over)
    os.makedirs(cfg["music_dir"], exist_ok=True)
    app = create_app(cfg)
    app.config["TESTING"] = True
    return app


def _login(client, password="s3cret"):
    client.post("/api/login", json={"password": password})
    return {"X-CSRF-Token": client.get("/api/me").get_json()["csrf_token"]}


def _flac(path, **tags):
    from mutagen.flac import FLAC
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sf.write(path, np.zeros(22050, dtype="float32"), 22050, format="FLAC")
    audio = FLAC(path)
    for k, v in tags.items():
        audio[k] = v
    audio.save()


def _insert(db_path, path, **cols):
    cols = {"status": "done", "analyzed_at": "2026-09-01T10:00:00+00:00", **cols}
    names = ", ".join(["file_path", *cols])
    marks = ", ".join("?" * (len(cols) + 1))
    conn = sqlite3.connect(db_path)
    cur = conn.execute(f"INSERT INTO tracks ({names}) VALUES ({marks})", [path, *cols.values()])
    tid = cur.lastrowid
    for name in [a.strip() for a in (cols.get("artist") or "").split(",") if a.strip()]:
        conn.execute("INSERT OR IGNORE INTO track_artists (track_id, name, norm_name) VALUES (?, ?, ?)",
                     (tid, name, name.lower()))
    conn.commit()
    conn.close()
    return tid


@pytest.fixture
def env(base_config):
    """An enabled app with an admin API key + password and a two-track album."""
    app = _app(base_config)
    client = app.test_client()
    csrf = _login(client)
    key = client.post("/api/subsonic/api-key", headers=csrf).get_json()["api_key"]
    pw = client.post("/api/subsonic/password", headers=csrf).get_json()["password"]
    music = base_config["music_dir"]
    a = os.path.join(music, "Artist", "Album", "01.flac")
    b = os.path.join(music, "Artist", "Album", "02.flac")
    _flac(a)
    _flac(b)
    t1 = _insert(base_config["db_path"], a, title="One", artist="Artist", album="Album",
                 album_artist="Artist", track_no=1, year=2020, duration_ms=180000, bpm=127.6)
    t2 = _insert(base_config["db_path"], b, title="Two", artist="Artist", album="Album",
                 album_artist="Artist", track_no=2, year=2020, duration_ms=200000, bpm=90.0)
    anon = app.test_client()  # Subsonic clients have no session cookie
    return {"app": app, "client": anon, "admin": client, "csrf": csrf, "key": key, "pw": pw,
            "t1": t1, "t2": t2, "paths": (a, b)}


def _get(env, method, fmt="json", **params):
    params = {"apiKey": env["key"], "v": "1.16.1", "c": "test", "f": fmt, **params}
    return env["client"].get(f"/rest/{method}", query_string=params)


def _json(resp):
    assert resp.status_code == 200
    return resp.get_json()["subsonic-response"]


# ── Opt-in ────────────────────────────────────────────────────────────────────

def test_disabled_means_absent(base_config):
    client = _app(base_config, subsonic_enabled=False).test_client()
    r = client.get("/rest/ping.view", query_string={"u": "admin", "p": "x"})
    assert r.status_code == 404
    assert b"Not Found" in r.data  # the plain 404, not the SPA shell


def test_requests_set_no_session_cookie(env):
    r = _get(env, "ping")
    assert "Set-Cookie" not in r.headers


# ── Auth ──────────────────────────────────────────────────────────────────────

def test_api_key_auth(env):
    body = _json(_get(env, "ping"))
    assert body["status"] == "ok" and body["openSubsonic"] is True
    assert body["version"] == "1.16.1" and body["type"] == "bpm-tagger"


def test_bad_api_key_is_error_44(env):
    body = _json(_get(env, "ping", apiKey="nope"))
    assert body["status"] == "failed" and body["error"]["code"] == 44


def test_token_auth(env):
    salt = "abc123"
    token = hashlib.md5((env["pw"] + salt).encode()).hexdigest()
    r = env["client"].get("/rest/ping.view", query_string={
        "u": "admin", "t": token, "s": salt, "v": "1.13.0", "c": "test", "f": "json"})
    assert _json(r)["status"] == "ok"


def test_wrong_token_is_error_40(env):
    r = env["client"].get("/rest/ping", query_string={
        "u": "admin", "t": "0" * 32, "s": "x", "f": "json"})
    assert _json(r)["error"]["code"] == 40


def test_web_password_never_works_for_subsonic(env):
    salt = "s"
    token = hashlib.md5(("s3cret" + salt).encode()).hexdigest()
    r = env["client"].get("/rest/ping", query_string={"u": "admin", "t": token, "s": salt, "f": "json"})
    assert _json(r)["error"]["code"] == 40


def test_plain_password_from_loopback_and_enc_form(env):
    for p in (env["pw"], "enc:" + env["pw"].encode().hex()):
        r = env["client"].get("/rest/ping", query_string={"u": "admin", "p": p, "f": "json"})
        assert _json(r)["status"] == "ok"


def test_plain_password_refused_from_public_address(env):
    r = env["client"].get("/rest/ping", query_string={"u": "admin", "p": env["pw"], "f": "json"},
                          environ_base={"REMOTE_ADDR": "8.8.8.8"})
    assert _json(r)["error"]["code"] == 42


def test_conflicting_auth_is_error_43(env):
    assert _json(_get(env, "ping", u="admin"))["error"]["code"] == 43


def test_missing_credentials_is_error_10(env):
    r = env["client"].get("/rest/ping", query_string={"f": "json"})
    assert _json(r)["error"]["code"] == 10


def test_failures_feed_the_login_lockout(env):
    for _ in range(6):
        _get(env, "ping", apiKey="wrong")
    body = _json(_get(env, "ping"))  # even the right key is refused while locked
    assert body["status"] == "failed" and "Too many" in body["error"]["message"]


def test_revoked_key_stops_working(env):
    env["admin"].delete("/api/subsonic/api-key", headers=env["csrf"])
    assert _json(_get(env, "ping"))["error"]["code"] == 44


# ── Envelope ──────────────────────────────────────────────────────────────────

def test_xml_is_the_default_format(env):
    r = _get(env, "getMusicFolders", fmt="xml")
    assert r.mimetype == "text/xml"
    root = ET.fromstring(r.data)
    assert root.tag == f"{NS}subsonic-response" and root.get("status") == "ok"
    folder = root.find(f"{NS}musicFolders/{NS}musicFolder")
    assert folder.get("id") == "1"


def test_xml_scalar_lists_become_child_elements(env):
    root = ET.fromstring(_get(env, "getUser", fmt="xml").data)
    assert [f.text for f in root.find(f"{NS}user").findall(f"{NS}folder")] == ["1"]


def test_unknown_method_fails_cleanly(env):
    body = _json(_get(env, "getPodcasts"))
    assert body["status"] == "failed" and body["error"]["code"] == 0


# ── Browsing ──────────────────────────────────────────────────────────────────

def test_artists_album_song_round_trip(env):
    idx = _json(_get(env, "getArtists"))["artists"]["index"]
    artist = idx[0]["artist"][0]
    assert idx[0]["name"] == "A" and artist["name"] == "Artist"

    art = _json(_get(env, "getArtist", id=artist["id"]))["artist"]
    assert art["albumCount"] == 1
    album = art["album"][0]
    assert album["name"] == "Album" and album["songCount"] == 2 and album["year"] == 2020

    alb = _json(_get(env, "getAlbum", id=album["id"]))["album"]
    songs = alb["song"]
    assert [s["title"] for s in songs] == ["One", "Two"]
    assert songs[0]["bpm"] == 128 and songs[0]["duration"] == 180
    assert songs[0]["path"] == "Artist/Album/01.flac"
    assert songs[0]["albumId"] == album["id"] and songs[0]["suffix"] == "flac"

    song = _json(_get(env, "getSong", id=songs[1]["id"]))["song"]
    assert song["title"] == "Two" and song["bpm"] == 90


def test_album_id_is_stable_and_normalized():
    assert ids.album_id("Album", "Artist") == ids.album_id("album", "ARTIST")
    assert ids.album_id("Album", "Artist") != ids.album_id("Album", "Other")


def test_unknown_ids_are_error_70(env):
    for method, bad in (("getSong", "tr-999999"), ("getAlbum", "al-0"), ("getArtist", "ar-0")):
        assert _json(_get(env, method, id=bad))["error"]["code"] == 70


def test_missing_id_is_error_10(env):
    assert _json(_get(env, "getAlbum"))["error"]["code"] == 10


def test_album_list_types(env):
    for kind in ("alphabeticalByName", "alphabeticalByArtist", "newest", "recent",
                 "frequent", "random", "byYear"):
        albums = _json(_get(env, "getAlbumList2", type=kind, fromYear=2019, toYear=2021))
        assert len(albums["albumList2"]["album"]) == 1, kind
    assert _json(_get(env, "getAlbumList2", type="starred"))["albumList2"]["album"] == []
    assert _json(_get(env, "getAlbumList2", type="byGenre", genre="x"))["albumList2"]["album"] == []


def test_search3_empty_query_pages_the_whole_library(env):
    res = _json(_get(env, "search3", query='""', songCount=1, songOffset=1))["searchResult3"]
    assert [s["title"] for s in res["song"]] == ["Two"]
    res = _json(_get(env, "search3", query="one"))["searchResult3"]
    assert [s["title"] for s in res["song"]] == ["One"]
    assert res["album"] == [] and res["artist"] == []


def test_random_songs(env):
    songs = _json(_get(env, "getRandomSongs", size=5))["randomSongs"]["song"]
    assert sorted(s["title"] for s in songs) == ["One", "Two"]


# ── Media ─────────────────────────────────────────────────────────────────────

def test_stream_serves_the_file_with_ranges(env):
    sid = f"tr-{env['t1']}"
    r = _get(env, "stream", id=sid)
    assert r.status_code == 200 and r.mimetype == "audio/flac"
    assert r.data == open(env["paths"][0], "rb").read()
    part = env["client"].get("/rest/stream", query_string={"apiKey": env["key"], "id": sid},
                             headers={"Range": "bytes=0-3"})
    assert part.status_code == 206 and part.data == b"fLaC"


def test_stream_refuses_paths_outside_music_dir(env, tmp_path):
    outside = tmp_path / "outside.flac"
    _flac(str(outside))
    tid = _insert(env["app"].extensions["state"].config["db_path"], str(outside), title="X")
    r = _get(env, "stream", id=f"tr-{tid}")
    assert r.status_code == 200 and r.get_json()["subsonic-response"]["status"] == "failed"


def test_cover_art_falls_back_to_folder_image(env):
    folder = os.path.dirname(env["paths"][0])
    with open(os.path.join(folder, "cover.jpg"), "wb") as f:
        f.write(b"\xff\xd8\xff\xe0fakejpeg")
    album = _json(_get(env, "getSong", id=f"tr-{env['t1']}"))["song"]["albumId"]
    for cid in (f"tr-{env['t1']}", album):
        r = _get(env, "getCoverArt", id=cid)
        assert r.status_code == 200 and r.mimetype == "image/jpeg"


def test_cover_art_missing_is_404(env):
    assert _get(env, "getCoverArt", id=f"tr-{env['t1']}").status_code == 404


# ── Annotation ────────────────────────────────────────────────────────────────

def test_star_unstar_and_starred2(env):
    sid = f"tr-{env['t1']}"
    assert _json(_get(env, "star", id=sid))["status"] == "ok"
    songs = _json(_get(env, "getStarred2"))["starred2"]["song"]
    assert [s["id"] for s in songs] == [sid] and songs[0]["starred"]
    assert len(_json(_get(env, "getAlbumList2", type="starred"))["albumList2"]["album"]) == 1
    _get(env, "unstar", id=sid)
    assert _json(_get(env, "getStarred2"))["starred2"]["song"] == []


def test_scrobble_counts_plays_and_ignores_now_playing(env):
    sid = f"tr-{env['t2']}"
    _get(env, "scrobble", id=sid, submission="false")
    _get(env, "scrobble", id=sid)
    song = _json(_get(env, "getSong", id=sid))["song"]
    assert song["playCount"] == 1
    db = env["app"].extensions["state"].db
    events = db.list_play_events()
    assert len(events) == 1 and events[0]["owner"] == "admin"


# ── Admin API ─────────────────────────────────────────────────────────────────

def test_status_and_restart_hint(base_config):
    app = _app(base_config, subsonic_enabled=False)
    client = app.test_client()
    csrf = _login(client)
    st = client.get("/api/subsonic").get_json()
    assert st["enabled"] is False and st["active"] is False and st["username"] == "admin"
    r = client.post("/api/subsonic/settings", json={"subsonic_enabled": True}, headers=csrf).get_json()
    assert r["enabled"] is True and r["restart_required"] is True


def test_credentials_are_never_readable_back(env):
    st = env["admin"].get("/api/subsonic").get_json()
    assert st["has_api_key"] and st["has_password"]
    assert env["key"] not in str(st) and env["pw"] not in str(st)
    assert env["key"] not in str(env["admin"].get("/api/settings").get_json())


def test_admin_routes_are_closed_to_the_player_role(base_config):
    client = _app(base_config, run_password="runpw").test_client()
    csrf = _login(client, "runpw")
    assert client.post("/api/subsonic/api-key", headers=csrf).status_code == 403
    assert client.get("/api/subsonic").status_code == 403
