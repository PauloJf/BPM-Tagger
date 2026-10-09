"""Subsonic API Phase 3: opt-in transcoding, Run presets as playlists, startScan
(docs/plans/subsonic-api.md)."""

import os
import shutil

import pytest

from bpm_tagger.web.subsonic import transcode
from test_subsonic import _app, _flac, _insert, _json, _login

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture
def env(base_config):
    def make(**over):
        app = _app(base_config, run_presets=[{"name": "Tempo", "bpm": 170}], **over)
        admin = app.test_client()
        csrf = _login(admin)
        key = admin.post("/api/subsonic/accounts/admin/api-key", headers=csrf).get_json()["api_key"]
        music, db_path = base_config["music_dir"], base_config["db_path"]
        tracks = {}
        for name, bpm in (("fast", 171.0), ("half", 86.0), ("slow", 120.0)):
            path = os.path.join(music, "A", "B", f"{name}.flac")
            if not os.path.exists(path):
                _flac(path)
            tracks[name] = _insert(db_path, path, title=name, artist="A", album="B",
                                   album_artist="A", bpm=bpm, duration_ms=1000)
        client = app.test_client()

        def call(method, key_=None, **params):
            return client.get(f"/rest/{method}",
                              query_string={"apiKey": key_ or key, "f": "json", **params})
        return {"app": app, "admin": admin, "csrf": csrf, "call": call, "ids": tracks,
                "st": app.extensions["state"]}
    return make


# ── Transcode planning (pure) ─────────────────────────────────────────────────

@pytest.fixture
def fake_ffmpeg(monkeypatch):
    monkeypatch.setattr(transcode, "ffmpeg_path", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(transcode, "_has_libopus", lambda: True)


@pytest.mark.parametrize("path,kbps,fmt,cap,expected", [
    ("a.flac", 900, "raw", 128, None),                # raw: never
    ("a.flac", 900, "", 0, None),                     # no format, no cap: as-is
    ("a.flac", 900, "", 128, ("opus", 128)),          # cap binds: default format
    ("a.mp3", 128, "", 192, None),                    # already under the cap
    ("a.flac", 900, "mp3", 0, ("mp3", 192)),          # explicit format, default bitrate
    ("a.mp3", 320, "mp3", 128, ("mp3", 128)),         # same format but over the cap
    ("a.mp3", 128, "mp3", 192, None),                 # same format, within the cap
    ("a.flac", 900, "mp3", 9999, ("mp3", 320)),       # clamped to 320
    ("a.flac", 900, "wav", 0, None),                  # unsupported format, no cap
])
def test_plan(fake_ffmpeg, path, kbps, fmt, cap, expected):
    assert transcode.plan(path, kbps, fmt, cap) == expected


def test_plan_without_ffmpeg_is_always_raw(monkeypatch):
    monkeypatch.setattr(transcode, "ffmpeg_path", lambda: None)
    assert transcode.plan("a.flac", 900, "mp3", 128) is None


def test_opus_falls_back_to_mp3_without_libopus(fake_ffmpeg, monkeypatch):
    monkeypatch.setattr(transcode, "_has_libopus", lambda: False)
    assert transcode.plan("a.flac", 900, "opus", 96) == ("mp3", 96)
    assert transcode.plan("a.flac", 900, "", 96) == ("mp3", 96)


# ── Streaming ─────────────────────────────────────────────────────────────────

def test_transcode_off_streams_the_original(env):
    e = env(subsonic_transcode=False)
    r = e["call"]("stream", id=f"tr-{e['ids']['fast']}", format="mp3", maxBitRate=64)
    assert r.mimetype == "audio/flac" and r.data[:4] == b"fLaC"


def test_download_never_transcodes(env):
    e = env(subsonic_transcode=True)
    r = e["call"]("download", id=f"tr-{e['ids']['fast']}", format="mp3")
    assert r.data[:4] == b"fLaC"


@needs_ffmpeg
@pytest.mark.parametrize("fmt,mime", [("mp3", "audio/mpeg"), ("opus", "audio/ogg")])
def test_transcode_to_requested_format(env, fmt, mime):
    e = env(subsonic_transcode=True)
    r = e["call"]("stream", id=f"tr-{e['ids']['fast']}", format=fmt, maxBitRate=96)
    data = r.get_data()
    assert r.status_code == 200 and r.mimetype == mime
    assert r.headers.get("Accept-Ranges") == "none"
    if fmt == "mp3":
        assert data[:3] == b"ID3" or data[0] == 0xFF
    else:
        assert data[:4] == b"OggS"


@needs_ffmpeg
def test_time_offset_skips_into_the_track(env):
    e = env(subsonic_transcode=True)
    sid = f"tr-{e['ids']['fast']}"
    whole = e["call"]("stream", id=sid, format="mp3", maxBitRate=128).get_data()
    tail = e["call"]("stream", id=sid, format="mp3", maxBitRate=128, timeOffset="0.6").get_data()
    assert 0 < len(tail) < len(whole)


@needs_ffmpeg
def test_slots_are_released_after_each_stream(env):
    e = env(subsonic_transcode=True)
    for _ in range(transcode.MAX_CONCURRENT + 2):
        r = e["call"]("stream", id=f"tr-{e['ids']['fast']}", format="mp3")
        assert r.mimetype == "audio/mpeg"
        r.get_data()
        r.close()


def test_busy_slots_fall_back_to_the_original(env, monkeypatch):
    import threading
    monkeypatch.setattr(transcode, "ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(transcode, "_has_libopus", lambda: True)
    monkeypatch.setattr(transcode, "_slots", threading.BoundedSemaphore(1))
    transcode._slots.acquire()  # every slot taken
    e = env(subsonic_transcode=True)
    r = e["call"]("stream", id=f"tr-{e['ids']['fast']}", format="mp3")
    assert r.mimetype == "audio/flac"


# ── Run presets as playlists ──────────────────────────────────────────────────

def test_run_presets_listed_as_readonly_playlists(env):
    e = env()
    pls = _json(e["call"]("getPlaylists"))["playlists"]["playlist"]
    run = [p for p in pls if p["id"].startswith("pl-run-")]
    assert len(run) == 1 and run[0]["name"] == "Run · Tempo (170 BPM)"
    assert run[0]["readonly"] is True and run[0]["songCount"] == 2
    entries = _json(e["call"]("getPlaylist", id=run[0]["id"]))["playlist"]["entry"]
    # 171 is 0.6 % off; 86 folds to 172 (1.2 %); 120 is out. Rating-weighted (D17)
    # draws a fresh random order each call, so only the set is asserted here.
    assert sorted(s["title"] for s in entries) == ["fast", "half"]


def test_run_playlists_are_not_writable(env):
    e = env()
    for method, params in (("updatePlaylist", {"playlistId": "pl-run-0", "name": "x"}),
                           ("deletePlaylist", {"id": "pl-run-0"})):
        assert _json(e["call"](method, **params))["error"]["code"] == 50


def test_run_playlists_can_be_turned_off(env):
    e = env(subsonic_run_playlists=False)
    pls = _json(e["call"]("getPlaylists"))["playlists"]["playlist"]
    assert not [p for p in pls if p["id"].startswith("pl-run-")]
    assert _json(e["call"]("getPlaylist", id="pl-run-0"))["error"]["code"] == 70


def test_run_playlist_for_a_player_uses_only_its_playlists(env):
    import sqlite3
    e = env()
    st = e["st"]
    track = st.db.get_track_by_id(e["ids"]["half"])
    conn = sqlite3.connect(st.config["db_path"])
    pid = conn.execute("INSERT INTO playlists (source, name, enabled) VALUES ('local', 'P', 1)").lastrowid
    conn.execute("INSERT INTO playlist_tracks (playlist_id, source_track_id, position, match_status, "
                 "matched_file_path) VALUES (?, ?, 0, 'have', ?)",
                 (pid, track["file_path"], track["file_path"]))
    conn.commit()
    conn.close()
    player = st.db.add_player("runner", "x", playlist_ids=[pid])
    key = e["admin"].post(f"/api/subsonic/accounts/player:{player}/api-key",
                          headers=e["csrf"]).get_json()["api_key"]
    entries = _json(e["call"]("getPlaylist", key_=key, id="pl-run-0"))["playlist"]["entry"]
    assert [s["title"] for s in entries] == ["half"]


# ── startScan ─────────────────────────────────────────────────────────────────

class _FakeTagger:
    def __init__(self):
        self.calls = []

    def scan_directory(self, force=False):
        self.calls.append(force)


def test_start_scan_runs_the_tagger(env):
    import time
    e = env()
    fake = _FakeTagger()
    e["st"].tagger = fake
    body = _json(e["call"]("startScan", fullScan="true"))
    assert body["scanStatus"]["scanning"] is True
    for _ in range(50):
        if fake.calls:
            break
        time.sleep(0.02)
    assert fake.calls == [True]


def test_start_scan_without_a_tagger_fails_cleanly(env):
    e = env()
    e["st"].tagger = None
    assert _json(e["call"]("startScan"))["error"]["code"] == 0


def test_player_cannot_start_a_scan(env):
    e = env()
    player = e["st"].db.add_player("p2", "x", playlist_ids=[])
    key = e["admin"].post(f"/api/subsonic/accounts/player:{player}/api-key",
                          headers=e["csrf"]).get_json()["api_key"]
    assert _json(e["call"]("startScan", key_=key))["error"]["code"] == 50


def test_admin_status_reports_phase3_settings(env):
    e = env(subsonic_transcode=True)
    st = e["admin"].get("/api/subsonic").get_json()
    assert st["transcode"] is True and st["run_playlists"] is True
    assert st["ffmpeg_available"] is (shutil.which("ffmpeg") is not None)
