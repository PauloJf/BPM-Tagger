"""On-demand LRCLIB lyrics for Subsonic apps (web/subsonic/lyrics_fetch.py) and
the OpenSubsonic tokenInfo method."""

import importlib
import os
import threading
import time

import pytest

from test_subsonic import _app, _flac, _insert, _json, _login

lf = importlib.import_module("bpm_tagger.web.subsonic.lyrics_fetch")

LRC = "[00:01.00]Left foot\n[00:02.00]Right foot"


@pytest.fixture
def env(base_config, monkeypatch):
    def make(fetch=True, answer=LRC, delay=0.0):
        calls = []

        def fake_fetch(artist, title, album="", duration_ms=None):
            calls.append((artist, title))
            time.sleep(delay)
            return None if answer is None else {"synced": answer, "plain": "", "instrumental": False}
        monkeypatch.setattr("bpm_tagger.integrations.lrclib.fetch_lyrics", fake_fetch)
        app = _app(base_config, subsonic_fetch_lyrics=fetch, lyrics_mode="sidecar")
        admin = app.test_client()
        csrf = _login(admin)
        key = admin.post("/api/subsonic/accounts/admin/api-key", headers=csrf).get_json()["api_key"]
        path = os.path.join(base_config["music_dir"], "A", "stride.flac")
        _flac(path)
        tid = _insert(base_config["db_path"], path, title="Stride", artist="Night Runners",
                      album="Tempo City", duration_ms=25000)
        client = app.test_client()

        def call(method, **params):
            return _json(client.get(f"/rest/{method}",
                                    query_string={"apiKey": key, "f": "json", **params}))
        return {"app": app, "call": call, "sid": f"tr-{tid}", "path": path, "calls": calls,
                "db": app.extensions["state"].db, "key": key}
    return make


def _lines(body):
    lists = body["lyricsList"]["structuredLyrics"]
    return [ln["value"] for ln in lists[0]["line"]] if lists else []


def test_off_by_default_never_calls_lrclib(env):
    e = env(fetch=False)
    assert _lines(e["call"]("getLyricsBySongId", id=e["sid"])) == []
    assert e["calls"] == []


def test_fetches_saves_and_serves(env):
    e = env()
    assert _lines(e["call"]("getLyricsBySongId", id=e["sid"])) == ["Left foot", "Right foot"]
    assert e["calls"] == [("Night Runners", "Stride")]
    assert os.path.isfile(os.path.splitext(e["path"])[0] + ".lrc")          # saved per lyrics_mode
    assert e["db"].get_track(e["path"])["lyrics_status"] == "fetched"
    # Served from the file from now on — no second lookup.
    assert _lines(e["call"]("getLyricsBySongId", id=e["sid"])) == ["Left foot", "Right foot"]
    assert len(e["calls"]) == 1


def test_legacy_get_lyrics_fetches_too(env):
    e = env()
    body = e["call"]("getLyrics", artist="Night Runners", title="Stride")
    assert body["lyrics"]["value"] == "Left foot\nRight foot"


def test_not_found_is_remembered(env):
    e = env(answer=None)
    assert _lines(e["call"]("getLyricsBySongId", id=e["sid"])) == []
    assert e["db"].get_track(e["path"])["lyrics_status"] == "not_found"
    e["call"]("getLyricsBySongId", id=e["sid"])
    assert len(e["calls"]) == 1


def test_slow_lookup_finishes_in_the_background(env, monkeypatch):
    monkeypatch.setattr(lf, "WAIT_S", 0.05)
    e = env(delay=0.4)
    assert _lines(e["call"]("getLyricsBySongId", id=e["sid"])) == []       # didn't wait
    for _ in range(50):
        if os.path.isfile(os.path.splitext(e["path"])[0] + ".lrc"):
            break
        time.sleep(0.05)
    assert _lines(e["call"]("getLyricsBySongId", id=e["sid"])) == ["Left foot", "Right foot"]
    assert len(e["calls"]) == 1


def test_concurrent_requests_share_one_lookup(env):
    e = env(delay=0.3)
    threads = [threading.Thread(target=e["call"], args=("getLyricsBySongId",),
                                kwargs={"id": e["sid"]}) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(e["calls"]) == 1


def test_busy_slots_skip_the_lookup(env, monkeypatch):
    monkeypatch.setattr(lf, "_slots", threading.BoundedSemaphore(1))
    lf._slots.acquire()
    e = env()
    assert _lines(e["call"]("getLyricsBySongId", id=e["sid"])) == []
    assert e["calls"] == []


def test_token_info_names_the_key_owner(env):
    e = env(fetch=False)
    assert e["call"]("tokenInfo")["tokenInfo"]["username"] == "admin"
    exts = {x["name"] for x in e["call"]("getOpenSubsonicExtensions")["openSubsonicExtensions"]}
    assert "apiKeyAuthentication" in exts
