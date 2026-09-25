"""Connected Subsonic apps + what they're playing (web/subsonic/activity.py),
getNowPlaying, and the admin /api/subsonic/clients list."""

import os

import pytest

import importlib

act_mod = importlib.import_module("bpm_tagger.web.subsonic.activity")
from bpm_tagger.web.subsonic.activity import Activity
from test_subsonic import _app, _flac, _insert, _json, _login

TRACK = {"id": 1, "title": "One", "artist": "Ann", "album": "A", "duration_ms": 180000}
OTHER = {"id": 2, "title": "Two", "artist": "Ann", "album": "A", "duration_ms": 200000}


# ── Registry semantics ───────────────────────────────────────────────────────

def test_report_beats_stream_inference():
    a = Activity()
    k = a.seen("admin", "admin", "Symfonium", "1.16.1", "10.0.0.2", "UA")
    a.streamed(k, TRACK)                       # no report yet → inferred
    assert a.snapshot()[0]["playing"]["source"] == "stream"
    a.now_playing_report(k, OTHER)
    snap = a.snapshot()[0]
    assert snap["playing"]["title"] == "Two" and snap["playing"]["source"] == "report"
    a.streamed(k, TRACK)                       # pre-fetch: ignored once the app reports
    assert a.snapshot()[0]["playing"]["title"] == "Two"


def test_a_seek_on_the_same_track_does_not_restart_it(monkeypatch):
    a = Activity()
    k = a.seen("admin", "admin", "DSub", "1.13.0", "10.0.0.3", "")
    clock = [1000.0]
    monkeypatch.setattr(act_mod.time, "time", lambda: clock[0])
    a.streamed(k, TRACK)
    clock[0] += 30
    a.streamed(k, TRACK)                       # range request for the same file
    assert a.snapshot()[0]["playing"]["elapsed_s"] == 30


def test_playing_expires_after_the_track_ends(monkeypatch):
    a = Activity()
    clock = [1000.0]
    monkeypatch.setattr(act_mod.time, "time", lambda: clock[0])
    k = a.seen("admin", "admin", "Feishin", "", "10.0.0.4", "")
    a.now_playing_report(k, TRACK)             # 180 s track
    clock[0] += 180 + act_mod.PLAYING_GRACE + 1
    snap = a.snapshot()[0]
    assert snap["playing"] is None and snap["last_played"]["title"] == "One"


def test_idle_clients_are_forgotten(monkeypatch):
    a = Activity()
    clock = [1000.0]
    monkeypatch.setattr(act_mod.time, "time", lambda: clock[0])
    a.seen("admin", "admin", "Old", "", "10.0.0.5", "")
    clock[0] += act_mod.CLIENT_TTL + 1
    assert a.snapshot() == []


def test_snapshot_can_be_limited_to_one_account():
    a = Activity()
    a.seen("admin", "admin", "X", "", "1.1.1.1", "")
    a.seen("player:3", "jog", "Y", "", "1.1.1.2", "")
    assert [c["app"] for c in a.snapshot("player:3")] == ["Y"]


def test_same_app_from_two_devices_is_two_clients():
    a = Activity()
    a.seen("admin", "admin", "Symfonium", "", "10.0.0.2", "")
    a.seen("admin", "admin", "symfonium", "", "10.0.0.9", "")
    a.seen("admin", "admin", "Symfonium", "", "10.0.0.2", "")
    assert len(a.snapshot()) == 2


# ── Through the API ──────────────────────────────────────────────────────────

@pytest.fixture
def env(base_config):
    act_mod.registry._clients.clear()
    app = _app(base_config)
    admin = app.test_client()
    csrf = _login(admin)
    key = admin.post("/api/subsonic/accounts/admin/api-key", headers=csrf).get_json()["api_key"]
    path = os.path.join(base_config["music_dir"], "A", "one.flac")
    _flac(path)
    tid = _insert(base_config["db_path"], path, title="One", artist="Ann", album="A",
                  album_artist="Ann", duration_ms=180000)
    client = app.test_client()

    def call(method, app_name="Symfonium", key_=None, **params):
        return client.get(f"/rest/{method}", query_string={
            "apiKey": key_ or key, "f": "json", "c": app_name, "v": "1.16.1", **params})
    yield {"app": app, "admin": admin, "csrf": csrf, "call": call, "sid": f"tr-{tid}"}
    act_mod.registry._clients.clear()


def test_clients_list_shows_app_and_now_playing(env):
    env["call"]("ping")
    clients = env["admin"].get("/api/subsonic/clients").get_json()["clients"]
    assert [(c["app"], c["username"], c["playing"]) for c in clients] == [("Symfonium", "admin", None)]

    env["call"]("scrobble", id=env["sid"], submission="false")
    playing = env["admin"].get("/api/subsonic/clients").get_json()["clients"][0]["playing"]
    assert playing["title"] == "One" and playing["source"] == "report"


def test_stream_counts_for_apps_that_never_report(env):
    env["call"]("stream", app_name="DSub", id=env["sid"]).close()
    c = [c for c in env["admin"].get("/api/subsonic/clients").get_json()["clients"] if c["app"] == "DSub"][0]
    assert c["playing"]["source"] == "stream"


def test_get_now_playing(env):
    env["call"]("scrobble", id=env["sid"], submission="false")
    entries = _json(env["call"]("getNowPlaying"))["nowPlaying"]["entry"]
    assert len(entries) == 1
    e = entries[0]
    assert e["title"] == "One" and e["username"] == "admin" and e["playerName"] == "Symfonium"
    assert e["minutesAgo"] == 0 and isinstance(e["playerId"], int)


def test_a_player_sees_only_its_own_now_playing(env):
    st = env["app"].extensions["state"]
    player = st.db.add_player("jog", "x", playlist_ids=[])
    pkey = env["admin"].post(f"/api/subsonic/accounts/player:{player}/api-key",
                             headers=env["csrf"]).get_json()["api_key"]
    env["call"]("scrobble", id=env["sid"], submission="false")   # the admin plays
    assert _json(env["call"]("getNowPlaying", key_=pkey))["nowPlaying"]["entry"] == []


def test_failed_auth_is_not_listed(env):
    env["call"]("ping", key_="wrong")
    assert env["admin"].get("/api/subsonic/clients").get_json()["clients"] == []


def test_clients_list_is_admin_only(base_config):
    client = _app(base_config, run_password="runpw").test_client()
    _login(client, "runpw")
    assert client.get("/api/subsonic/clients").status_code == 403
