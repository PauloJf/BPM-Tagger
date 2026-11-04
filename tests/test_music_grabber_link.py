"""Music Grabber link: settings, requests from playlists and the Related panel,
status refresh, roles. Music Grabber itself is a fake requests.Session."""

import sqlite3

import pytest
from conftest import _make_app

from bpm_tagger.integrations import music_grabber as mg


@pytest.fixture
def make_app():
    return _make_app


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class FakeGrabber:
    """Records calls; answers like Music Grabber's /api/v1."""

    def __init__(self):
        self.calls, self.items, self.next_id = [], {}, 1
        self.auth_status = None          # set to 401/403 to refuse the key

    def request(self, method, url, timeout=None, headers=None, json=None, params=None):
        self.calls.append((method, url.split("/api/v1")[1], json, params, headers))
        if self.auth_status:
            return FakeResponse(self.auth_status, {"error": "unauthorized"})
        if method == "POST":
            iid = self.next_id
            self.next_id += 1
            self.items[iid] = {"id": iid, "status": "pending", "needs_choice": False, "path": None, "error": None}
            return FakeResponse(201, {"ok": True, "result": "queued", "id": iid, "status": "pending"})
        ids = (params or {}).get("ids")
        if not ids:
            return FakeResponse(400, {"error": "no_ids", "message": "send ids"})
        want = [int(x) for x in ids.split(",")]
        return FakeResponse(200, {"requests": [self.items[i] for i in want if i in self.items]})


@pytest.fixture
def fake(monkeypatch):
    f = FakeGrabber()
    monkeypatch.setattr(mg.requests, "Session", lambda: f)
    monkeypatch.setattr(mg, "REFRESH_AFTER_S", 0)
    return f


@pytest.fixture
def linked(base_config):
    base_config.update(music_grabber_url="http://music-grabber:8099/",
                       music_grabber_api_key="mgk_secret_key_value",
                       music_grabber_public_url="https://grab.example")
    return base_config


def _admin(app):
    c = app.test_client()
    assert c.post("/api/login", json={"password": "s3cret"}).status_code == 200
    return c, {"X-CSRF-Token": c.get("/api/me").get_json()["csrf_token"]}


def _account(app, admin, csrf, name, role):
    admin.post("/api/players", json={"username": name, "password": "longpass1", "role": role}, headers=csrf)
    c = app.test_client()
    assert c.post("/api/login", json={"username": name, "password": "longpass1"}).status_code == 200
    return c, {"X-CSRF-Token": c.get("/api/me").get_json()["csrf_token"]}


def _missing_rows(db_path, n=2):
    conn = sqlite3.connect(db_path)
    pid = conn.execute("INSERT INTO playlists (source, name, enabled, created_at) VALUES ('local','Mix',1,'')").lastrowid
    ids = []
    for i in range(n):
        ids.append(conn.execute(
            "INSERT INTO playlist_tracks (playlist_id, source_track_id, title, artist, album, isrc, match_status, position) "
            "VALUES (?, ?, ?, 'Artist', 'Album', ?, 'missing', ?)",
            (pid, f"s{i}", f"Song {i}", "GBAAA000000%d" % i if i == 0 else None, i)).lastrowid)
    conn.commit()
    conn.close()
    return ids


def test_inert_until_configured(app, fake):
    c, csrf = _admin(app)
    assert c.get("/api/grabber/status").get_json() == {"configured": False, "public_url": "", "can_request": False}
    assert c.post("/api/grabber/request", json={"dz_id": "1"}, headers=csrf).status_code == 400
    assert fake.calls == []


def test_settings_save_and_mask_the_key(app, fake):
    c, csrf = _admin(app)
    r = c.post("/api/grabber/settings", headers=csrf, json={
        "music_grabber_url": "http://music-grabber:8099", "music_grabber_api_key": "mgk_abc",
        "music_grabber_public_url": "ftp://nope"})
    assert r.status_code == 400
    r = c.post("/api/grabber/settings", headers=csrf, json={
        "music_grabber_url": "http://music-grabber:8099/", "music_grabber_api_key": "mgk_abc"})
    assert r.get_json()["configured"] is True
    settings = c.get("/api/settings").get_json()["settings"]
    assert settings["music_grabber_api_key"] == "********"
    assert settings["music_grabber_url"] == "http://music-grabber:8099"
    # Saving again with the mask keeps the key.
    c.post("/api/grabber/settings", headers=csrf, json={
        "music_grabber_url": "http://music-grabber:8099", "music_grabber_api_key": "********"})
    assert app.extensions["state"].config["music_grabber_api_key"] == "mgk_abc"
    st = c.get("/api/grabber/status").get_json()
    assert st == {"configured": True, "public_url": "http://music-grabber:8099", "can_request": True}


def test_connection_test_reports_bad_key(make_app, linked, fake):
    app = make_app(linked)
    c, csrf = _admin(app)
    assert c.post("/api/grabber/test", headers=csrf).get_json() == {"ok": True}
    fake.auth_status = 401
    r = c.post("/api/grabber/test", headers=csrf)
    assert r.status_code == 502 and r.get_json()["error"] == "bad_key"


def test_request_missing_playlist_rows_is_idempotent(make_app, linked, fake):
    app = make_app(linked)
    c, csrf = _admin(app)
    ids = _missing_rows(linked["db_path"])
    r = c.post("/api/grabber/request", json={"playlist_track_ids": ids}, headers=csrf)
    assert r.status_code == 200
    got = r.get_json()["requests"]
    assert set(got) == {f"pt:{i}" for i in ids} and all(v["status"] == "pending" and v["active"] for v in got.values())
    posts = [call for call in fake.calls if call[0] == "POST"]
    assert posts[0][2]["isrc"] == "GBAAA0000000" and posts[0][2]["source_ref"] == f"bpm-tagger:pt:{ids[0]}"
    assert posts[1][2]["title"] == "Song 1" and "isrc" not in posts[1][2]
    assert posts[0][2]["requested_for"] == "admin"
    assert posts[0][4]["Authorization"] == "Bearer mgk_secret_key_value"
    # Asking again doesn't send anything new while the first is in flight.
    c.post("/api/grabber/request", json={"playlist_track_ids": ids}, headers=csrf)
    assert len([call for call in fake.calls if call[0] == "POST"]) == 2


def test_refresh_links_a_finished_file_to_its_playlist_row(make_app, linked, fake):
    app = make_app(linked)
    c, csrf = _admin(app)
    [pt] = _missing_rows(linked["db_path"], 1)
    c.post("/api/grabber/request", json={"playlist_track_ids": [pt]}, headers=csrf)
    full = f"{linked['music_dir']}/Artist/Album/01 - Song 0.mp3"
    conn = sqlite3.connect(linked["db_path"])
    conn.execute("INSERT INTO tracks (file_path, title, artist, status) VALUES (?, 'Song 0', 'Artist', 'done')", (full,))
    conn.commit()
    conn.close()
    fake.items[1].update(status="done", path="Artist/Album/01 - Song 0.mp3")
    states = c.get(f"/api/grabber/requests?refs=pt:{pt},pt:999").get_json()["requests"]
    assert list(states) == [f"pt:{pt}"] and states[f"pt:{pt}"]["status"] == "done"
    assert states[f"pt:{pt}"]["active"] is False
    conn = sqlite3.connect(linked["db_path"])
    row = conn.execute("SELECT match_status, matched_file_path FROM playlist_tracks WHERE id=?", (pt,)).fetchone()
    conn.close()
    assert row == ("have", full)


def test_a_path_outside_the_music_folder_is_never_linked(make_app, linked, fake):
    app = make_app(linked)
    c, csrf = _admin(app)
    [pt] = _missing_rows(linked["db_path"], 1)
    c.post("/api/grabber/request", json={"playlist_track_ids": [pt]}, headers=csrf)
    fake.items[1].update(status="done", path="../../etc/passwd")
    c.get(f"/api/grabber/requests?refs=pt:{pt}")
    conn = sqlite3.connect(linked["db_path"])
    row = conn.execute("SELECT match_status FROM playlist_tracks WHERE id=?", (pt,)).fetchone()
    conn.close()
    assert row[0] == "missing"


def test_catalogue_track_request_and_needs_choice(make_app, linked, fake):
    app = make_app(linked)
    c, csrf = _admin(app)
    r = c.post("/api/grabber/request", json={"dz_id": "3135556", "title": "T", "artist": "A"}, headers=csrf)
    assert r.get_json()["requests"]["dz:3135556"]["status"] == "pending"
    assert fake.calls[-1][2]["dz_id"] == "3135556"
    assert c.post("/api/grabber/request", json={"dz_id": "x1"}, headers=csrf).status_code == 400
    fake.items[1].update(status="awaiting_user", needs_choice=True)
    s = c.get("/api/grabber/requests?refs=dz:3135556").get_json()["requests"]["dz:3135556"]
    assert s["needs_choice"] is True and s["active"] is True


def test_members_may_request_unless_turned_off_and_players_never(make_app, linked, fake):
    app = make_app(linked)
    admin, acsrf = _admin(app)
    m, mcsrf = _account(app, admin, acsrf, "ana", "member")
    assert m.get("/api/grabber/status").get_json()["can_request"] is True
    r = m.post("/api/grabber/request", json={"dz_id": "42"}, headers=mcsrf)
    assert r.status_code == 200 and fake.calls[-1][2]["requested_for"] == "ana"
    assert m.post("/api/grabber/settings", json={}, headers=mcsrf).status_code == 403
    assert m.post("/api/grabber/test", headers=mcsrf).status_code == 403
    admin.post("/api/grabber/settings", headers=acsrf, json={
        "music_grabber_url": "http://music-grabber:8099", "music_grabber_api_key": "********",
        "music_grabber_members_request": False})
    assert m.get("/api/grabber/status").get_json()["can_request"] is False
    assert m.post("/api/grabber/request", json={"dz_id": "43"}, headers=mcsrf).status_code == 403
    p, pcsrf = _account(app, admin, acsrf, "kid", "player")
    assert p.get("/api/grabber/status").status_code == 403
    assert p.post("/api/grabber/request", json={"dz_id": "44"}, headers=pcsrf).status_code == 403


def test_unreachable_grabber_is_reported_not_raised(make_app, linked, monkeypatch):
    import requests as rq

    class Down:
        def request(self, *a, **k):
            raise rq.ConnectionError("refused")
    monkeypatch.setattr(mg.requests, "Session", lambda: Down())
    app = make_app(linked)
    c, csrf = _admin(app)
    r = c.post("/api/grabber/request", json={"dz_id": "1"}, headers=csrf)
    assert r.status_code == 502 and r.get_json()["errors"]["dz:1"]["error"] == "unreachable"
