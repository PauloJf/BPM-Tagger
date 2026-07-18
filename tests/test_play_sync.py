"""Navidrome play-count pull (integrations.play_sync) and /api/scrobble.

Driver tests monkeypatch the Subsonic client functions in the module namespace
and run against a real temp BPMDatabase, mirroring test_star_sync.py.
"""

import sqlite3

import pytest

from bpm_tagger.db import BPMDatabase
from bpm_tagger.grabber import matching as m
from bpm_tagger.integrations import navidrome as nd
from bpm_tagger.integrations import play_sync as ps

CFG = {"navidrome_url": "http://nav:4533", "navidrome_user": "u", "navidrome_pass": "p"}


def _seed(db, path, title, artist, duration_ms=200000):
    db.upsert_track(path, "1:1", 120.0, None, None, 120.0, 0.9, "librosa", "done")
    db.update_track_tags(path, {
        "title": title, "artist": artist, "album": "Alb", "album_artist": artist,
        "track_no": 1, "disc_no": 1, "year": 2020, "isrc": "", "duration_ms": duration_ms,
        "norm_title": m.normalize_title(title), "norm_artist": m.normalize_artist(artist),
    }, "1:1")


def _song(sid, path, title="T", artist="A", play_count=0, played=None, duration_s=200):
    s = {"id": sid, "path": path, "title": title, "artist": artist,
         "album": "Alb", "duration": duration_s, "playCount": play_count}
    if played:
        s["played"] = played
    return s


@pytest.fixture
def db(tmp_path):
    return BPMDatabase(str(tmp_path / "s.db"))


def _track(db, path):
    return db.get_track(path)


# ── pull driver ───────────────────────────────────────────────────────────────

def test_unconfigured_returns_error(db):
    res = ps.pull_play_counts(db, {})
    assert res["ok"] is False and "configured" in res["error"]


def test_pull_updates_matched_rows(db, monkeypatch):
    _seed(db, "/data/music/A/one.mp3", "One", "Alpha")
    _seed(db, "/data/music/A/two.mp3", "Two", "Alpha")
    songs = [_song("nd1", "/music/A/one.mp3", "One", "Alpha",
                   play_count=7, played="2026-07-01T06:30:00Z"),
             _song("nd2", "/music/A/two.mp3", "Two", "Alpha", play_count=0)]
    monkeypatch.setattr(ps, "iter_all_songs", lambda url, user, pwd: iter(songs))

    res = ps.pull_play_counts(db, CFG)

    assert res["ok"] and res["matched"] == 2 and res["updated"] == 2
    assert res["unmatched_remote"] == 0
    one = _track(db, "/data/music/A/one.mp3")
    assert one["play_count"] == 7
    assert one["last_played"] == "2026-07-01T06:30:00Z"
    assert one["nd_song_id"] == "nd1"       # id cache warmed for the star sync
    assert _track(db, "/data/music/A/two.mp3")["play_count"] == 0


def test_pull_raises_count_on_higher_remote(db, monkeypatch):
    _seed(db, "/data/music/A/one.mp3", "One", "Alpha")
    monkeypatch.setattr(ps, "iter_all_songs",
                        lambda url, user, pwd: iter([_song("nd1", "/music/A/one.mp3",
                                                           "One", "Alpha", play_count=3)]))
    ps.pull_play_counts(db, CFG)
    assert _track(db, "/data/music/A/one.mp3")["play_count"] == 3
    monkeypatch.setattr(ps, "iter_all_songs",
                        lambda url, user, pwd: iter([_song("nd1", "/music/A/one.mp3",
                                                           "One", "Alpha", play_count=9)]))
    ps.pull_play_counts(db, CFG)
    assert _track(db, "/data/music/A/one.mp3")["play_count"] == 9


def test_pull_keeps_local_count_when_remote_lower(db, monkeypatch):
    """Plays counted locally (e.g. while Navidrome was disconnected) survive a
    pull that returns a lower remote total — the merge takes the MAX."""
    _seed(db, "/data/music/A/one.mp3", "One", "Alpha")
    for _ in range(3):
        db.bump_play_count("/data/music/A/one.mp3")   # local tally = 3
    monkeypatch.setattr(ps, "iter_all_songs",
                        lambda url, user, pwd: iter([_song("nd1", "/music/A/one.mp3",
                                                           "One", "Alpha", play_count=1)]))
    ps.pull_play_counts(db, CFG)
    assert _track(db, "/data/music/A/one.mp3")["play_count"] == 3


def test_unmatched_remote_song_is_counted_not_fatal(db, monkeypatch):
    _seed(db, "/data/music/A/one.mp3", "One", "Alpha")
    songs = [_song("ndX", "/elsewhere/unknown.mp3", "Nothing Like It", "Nobody", play_count=5)]
    monkeypatch.setattr(ps, "iter_all_songs", lambda url, user, pwd: iter(songs))

    res = ps.pull_play_counts(db, CFG)

    assert res["ok"] and res["matched"] == 0 and res["unmatched_remote"] == 1
    assert _track(db, "/data/music/A/one.mp3")["play_count"] is None


def test_walk_failure_surfaces_error(db, monkeypatch):
    def boom(url, user, pwd):
        raise RuntimeError("connection refused")
        yield  # pragma: no cover — make it a generator
    monkeypatch.setattr(ps, "iter_all_songs", boom)
    res = ps.pull_play_counts(db, CFG)
    assert res["ok"] is False and "connection refused" in res["error"]


def test_iter_all_songs_pages_until_short_page(monkeypatch):
    """Paging stops on the first page shorter than page_size; offsets advance."""
    calls = []

    class Resp:
        def __init__(self, songs):
            self._songs = songs
        def raise_for_status(self):
            pass
        def json(self):
            return {"subsonic-response": {"status": "ok",
                                          "searchResult3": {"song": self._songs}}}

    pages = [[_song(f"a{i}", f"/m/a{i}.mp3") for i in range(2)],
             [_song("b0", "/m/b0.mp3")]]

    def fake_get(url, params=None, timeout=None):
        calls.append(params["songOffset"])
        return Resp(pages[len(calls) - 1])

    monkeypatch.setattr(nd.requests, "get", fake_get)
    songs = list(nd.iter_all_songs("http://nav:4533", "u", "p", page_size=2))
    assert [s["id"] for s in songs] == ["a0", "a1", "b0"]
    assert calls == [0, 2]


# ── /api/scrobble route ───────────────────────────────────────────────────────

def _login(client):
    assert client.post("/api/login", json={"password": "s3cret"}).status_code == 200
    return {"X-CSRF-Token": client.get("/api/me").get_json()["csrf_token"]}


def _seed_row(db_path, music_dir, name="song", nd_song_id=None, play_count=None):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO tracks (file_path, title, artist, bpm, status, nd_song_id, play_count) "
        "VALUES (?, ?, 'Artist', 120.0, 'done', ?, ?)",
        (f"{music_dir}/{name}.mp3", name, nd_song_id, play_count))
    conn.commit()
    conn.close()
    return f"{music_dir}/{name}.mp3"


def _enable_scrobbling(client, csrf):
    r = client.post("/api/settings/navidrome",
                    json={"navidrome_url": "http://nav:4533", "navidrome_user": "u",
                          "navidrome_pass": "p", "navidrome_scrobble": True},
                    headers=csrf)
    assert r.status_code == 200


def test_scrobble_requires_csrf(client):
    _login(client)
    assert client.post("/api/scrobble", json={"path": "/x.mp3"}).status_code == 403


def test_scrobble_disabled_still_counts_locally(client, base_config):
    """With Navidrome scrobbling off, the play is still counted locally (+1) and
    reported as ok=True / forwarded=False — play counts work without Navidrome."""
    csrf = _login(client)
    path = _seed_row(base_config["db_path"], base_config["music_dir"], play_count=2)
    r = client.post("/api/scrobble", json={"path": path}, headers=csrf)
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and body["forwarded"] is False
    conn = sqlite3.connect(base_config["db_path"])
    (pc,) = conn.execute("SELECT play_count FROM tracks WHERE file_path = ?", (path,)).fetchone()
    conn.close()
    assert pc == 3


def test_scrobble_with_cached_id_bumps_local_count(client, base_config, monkeypatch):
    csrf = _login(client)
    _enable_scrobbling(client, csrf)
    path = _seed_row(base_config["db_path"], base_config["music_dir"],
                     nd_song_id="nd1", play_count=4)
    sent = []
    monkeypatch.setattr(nd, "scrobble", lambda url, user, pwd, sid: sent.append(sid) or True)

    r = client.post("/api/scrobble", json={"path": path}, headers=csrf)

    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert sent == ["nd1"]
    from urllib.parse import quote
    track = client.get(f"/api/track?path={quote(path)}").get_json()["track"]
    assert track["play_count"] == 5


def test_scrobble_resolves_and_caches_missing_id(client, base_config, monkeypatch):
    csrf = _login(client)
    _enable_scrobbling(client, csrf)
    path = _seed_row(base_config["db_path"], base_config["music_dir"])
    monkeypatch.setattr(nd, "resolve_id", lambda url, user, pwd, track, threshold=0.80: "nd9")
    monkeypatch.setattr(nd, "scrobble", lambda url, user, pwd, sid: True)

    r = client.post("/api/scrobble", json={"path": path}, headers=csrf)

    assert r.status_code == 200 and r.get_json()["ok"] is True
    conn = sqlite3.connect(base_config["db_path"])
    sid, pc = conn.execute("SELECT nd_song_id, play_count FROM tracks WHERE file_path = ?",
                           (path,)).fetchone()
    conn.close()
    assert sid == "nd9" and pc == 1     # first play: NULL count starts at 0


def test_scrobble_unresolvable_still_counts_locally(client, base_config, monkeypatch):
    """Unmatched in Navidrome ⇒ not forwarded, but still counted locally."""
    csrf = _login(client)
    _enable_scrobbling(client, csrf)
    path = _seed_row(base_config["db_path"], base_config["music_dir"], play_count=1)
    monkeypatch.setattr(nd, "resolve_id", lambda url, user, pwd, track, threshold=0.80: None)

    r = client.post("/api/scrobble", json={"path": path}, headers=csrf)

    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and body["forwarded"] is False and "not matched" in body["forward_error"]
    conn = sqlite3.connect(base_config["db_path"])
    (pc,) = conn.execute("SELECT play_count FROM tracks WHERE file_path = ?", (path,)).fetchone()
    conn.close()
    assert pc == 2


def test_scrobble_remote_failure_still_counts_locally(client, base_config, monkeypatch):
    """A rejected remote scrobble doesn't undo the local +1 (200, forwarded=False)."""
    csrf = _login(client)
    _enable_scrobbling(client, csrf)
    path = _seed_row(base_config["db_path"], base_config["music_dir"],
                     nd_song_id="nd1", play_count=4)
    monkeypatch.setattr(nd, "scrobble", lambda url, user, pwd, sid: False)

    r = client.post("/api/scrobble", json={"path": path}, headers=csrf)

    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and body["forwarded"] is False
    conn = sqlite3.connect(base_config["db_path"])
    (pc,) = conn.execute("SELECT play_count FROM tracks WHERE file_path = ?", (path,)).fetchone()
    conn.close()
    assert pc == 5


def test_scrobble_outside_music_dir_is_403(client, base_config, monkeypatch):
    csrf = _login(client)
    _enable_scrobbling(client, csrf)
    r = client.post("/api/scrobble", json={"path": "/etc/passwd"}, headers=csrf)
    assert r.status_code == 403


def test_scrobble_unknown_track_is_404(client, base_config):
    csrf = _login(client)
    _enable_scrobbling(client, csrf)
    r = client.post("/api/scrobble", json={"path": f"{base_config['music_dir']}/nope.mp3"},
                    headers=csrf)
    assert r.status_code == 404


# ── pull route ────────────────────────────────────────────────────────────────

def test_sync_play_counts_route_returns_counts(client, monkeypatch):
    csrf = _login(client)
    monkeypatch.setattr(ps, "pull_play_counts",
                        lambda db, cfg: {"ok": True, "remote_songs": 10, "matched": 8,
                                         "updated": 8, "unmatched_remote": 2})
    r = client.post("/api/settings/sync-play-counts", headers=csrf)
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and body["matched"] == 8 and body["unmatched_remote"] == 2


def test_sync_play_counts_route_unconfigured_is_502(client):
    csrf = _login(client)
    r = client.post("/api/settings/sync-play-counts", headers=csrf)
    assert r.status_code == 502 and r.get_json()["ok"] is False


def test_navidrome_settings_persist_scrobble_flag(client, base_config):
    csrf = _login(client)
    _enable_scrobbling(client, csrf)
    s = client.get("/api/settings").get_json()["settings"]
    assert s["navidrome_scrobble"] is True
    import json
    from pathlib import Path
    saved = json.loads((Path(base_config["db_path"]).parent / "settings.json").read_text())
    assert saved["navidrome_scrobble"] is True
