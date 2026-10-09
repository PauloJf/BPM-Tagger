"""Navidrome two-way rating sync: merge truth table, driver, star-sync
push-only interaction, periodic-job gating, and the API route. Mirrors
tests/test_star_sync.py — see docs/plans/ratings-weighted-picking.md §
"Navidrome rating sync" (D15/D16).
"""

import pytest

from bpm_tagger.db import BPMDatabase
from bpm_tagger.grabber import matching as m
from bpm_tagger.integrations import periodic_sync as ps
from bpm_tagger.integrations import rating_sync as rs
from bpm_tagger.integrations import star_sync as ss

# ── merge_rating: the pure three-way merge ───────────────────────────────────

@pytest.mark.parametrize("local,remote,base,final,action", [
    # agreement → nothing, regardless of base
    (None, None, None, None, "none"),
    (3,    3,    None, 3,    "none"),
    (3,    3,    2,    3,    "none"),
    # local changed, remote didn't → push local outward
    (4,    None, None, 4,    "push"),   # rated here, never synced (base None)
    (4,    2,    2,    4,    "push"),   # rated here since last sync
    (None, 2,    2,    None, "push"),   # cleared here since last sync
    # remote changed, local didn't → pull remote in
    (None, 5,    None, 5,    "pull"),   # rated in Navidrome, never synced
    (2,    5,    2,    5,    "pull"),   # rated in Navidrome since last sync
    (2,    None, 2,    None, "pull"),   # cleared in Navidrome since last sync
    # both changed and disagree → local wins (D16)
    (5,    3,    None, 5,    "conflict"),  # first sync, both sides rated differently
    (5,    3,    2,    5,    "conflict"),  # both changed since last sync, disagreeing
    (None, 3,    2,    None, "conflict"),  # local cleared, remote changed to something else
])
def test_merge_rating_truth_table(local, remote, base, final, action):
    assert rs.merge_rating(local, remote, base) == (final, action)


# ── driver fixtures ──────────────────────────────────────────────────────────

CFG = {"navidrome_url": "http://nav:4533", "navidrome_user": "u", "navidrome_pass": "p"}
RATING_CFG = {**CFG, "navidrome_sync_ratings": True}


def _seed(db, path, title, artist, rating=None, duration_ms=200000):
    db.upsert_track(path, "1:1", 120.0, None, None, 120.0, 0.9, "librosa", "done")
    db.update_track_tags(path, {
        "title": title, "artist": artist, "album": "Alb", "album_artist": artist,
        "track_no": 1, "disc_no": 1, "year": 2020, "isrc": "", "duration_ms": duration_ms,
        "norm_title": m.normalize_title(title), "norm_artist": m.normalize_artist(artist),
    }, "1:1")
    if rating is not None:
        db.set_rating("admin", path, rating)


def _song(sid, path, title="T", artist="A", duration_s=200, user_rating=0):
    return {"id": sid, "path": path, "title": title, "artist": artist,
            "album": "Alb", "duration": duration_s, "userRating": user_rating}


@pytest.fixture
def db(tmp_path):
    return BPMDatabase(str(tmp_path / "s.db"))


@pytest.fixture
def remote(monkeypatch):
    """Fake Subsonic server state for the rating-sync module: .songs is the
    full library walk (iter_all_songs); .ratings records setRating writes;
    .fail makes every setRating call fail."""
    class Remote:
        songs: list = []
        ratings: dict = {}       # sid -> rating written
        fail = False
        resolve: dict = {}       # (artist, title) -> id for search3 resolution

        def iter_all_songs(self, url, user, pwd, page_size=500):
            yield from self.songs

        def set_rating(self, url, user, pwd, sid, rating):
            if self.fail:
                return False
            self.ratings[sid] = rating
            return True

        def resolve_id(self, url, user, pwd, track, threshold=0.80):
            return self.resolve.get((track.get("artist"), track.get("title")))

    r = Remote()
    monkeypatch.setattr(rs, "iter_all_songs", r.iter_all_songs)
    monkeypatch.setattr(rs, "set_rating", r.set_rating)
    monkeypatch.setattr(rs, "resolve_id", r.resolve_id)
    return r


def _row(db, path):
    return next(r for r in db.all_admin_ratings_for_sync() if r["file_path"] == path)


# ── driver behavior ──────────────────────────────────────────────────────────

def test_unconfigured_returns_error(db):
    res = rs.sync_ratings(db, {})
    assert res["ok"] is False and "configured" in res["error"]


def test_first_run_pull(db, remote):
    _seed(db, "/data/music/A/t.mp3", "T", "Alpha")     # never rated locally
    remote.songs = [_song("nd1", "/music/A/t.mp3", "T", "Alpha", user_rating=5)]

    res = rs.sync_ratings(db, CFG)

    assert res["ok"] and res["pulled"] == 1 and res["pushed"] == 0 and res["conflicts"] == 0
    row = _row(db, "/data/music/A/t.mp3")
    assert row["rating"] == 5 and row["rating_base"] == 5 and row["nd_song_id"] == "nd1"
    assert remote.ratings == {}   # no remote write on a pull


def test_first_run_push(db, remote):
    _seed(db, "/data/music/A/t.mp3", "T", "Alpha", rating=4)
    remote.songs = [_song("nd1", "/music/A/t.mp3", "T", "Alpha", user_rating=0)]

    res = rs.sync_ratings(db, CFG)

    assert res["pushed"] == 1 and res["pulled"] == 0
    assert remote.ratings == {"nd1": 4}
    row = _row(db, "/data/music/A/t.mp3")
    assert row["rating"] == 4 and row["rating_base"] == 4


def test_conflict_local_wins(db, remote):
    _seed(db, "/data/music/A/t.mp3", "T", "Alpha", rating=5)
    db.set_rating_synced("/data/music/A/t.mp3", 5, nd_song_id="nd1")  # base=5, synced before
    db.set_rating("admin", "/data/music/A/t.mp3", 2)                  # changed locally since
    remote.songs = [_song("nd1", "/music/A/t.mp3", "T", "Alpha", user_rating=4)]  # changed remotely too

    res = rs.sync_ratings(db, CFG)

    assert res["conflicts"] == 1 and res["pushed"] == 0 and res["pulled"] == 0
    assert remote.ratings == {"nd1": 2}   # local (2) pushed out, winning the conflict
    row = _row(db, "/data/music/A/t.mp3")
    assert row["rating"] == 2 and row["rating_base"] == 2


def test_failed_remote_write_keeps_baseline_for_retry(db, remote):
    _seed(db, "/data/music/A/t.mp3", "T", "Alpha", rating=4)
    remote.resolve[("Alpha", "T")] = "nd1"
    remote.fail = True

    res = rs.sync_ratings(db, CFG)

    assert res["errors"] == 1 and res["pushed"] == 0
    row = _row(db, "/data/music/A/t.mp3")
    assert row["rating"] == 4 and row["rating_base"] is None  # untouched → retried next run

    remote.fail = False
    res2 = rs.sync_ratings(db, CFG)
    assert res2["pushed"] == 1 and remote.ratings == {"nd1": 4}


def test_pull_clears_local_rating(db, remote):
    _seed(db, "/data/music/A/t.mp3", "T", "Alpha", rating=4)
    db.set_rating_synced("/data/music/A/t.mp3", 4, nd_song_id="nd1")  # base=4, synced before
    remote.songs = [_song("nd1", "/music/A/t.mp3", "T", "Alpha", user_rating=0)]  # cleared remotely

    res = rs.sync_ratings(db, CFG)

    assert res["pulled"] == 1
    row = _row(db, "/data/music/A/t.mp3")
    assert row["rating"] is None and row["rating_base"] is None
    assert row["starred"] == 0 if "starred" in row else True  # projection follows (rating < 4)


def test_unmatched_track_is_never_pulled_to_unrated(db, remote):
    """A track missing from the walk (renamed, moved, match failed) has an
    UNKNOWN remote rating — it must not read as "cleared remotely" and wipe
    the local rating."""
    _seed(db, "/data/music/A/t.mp3", "T", "Alpha", rating=4)
    db.set_rating_synced("/data/music/A/t.mp3", 4, nd_song_id="nd1")  # base=4
    remote.songs = []                                                 # not in this walk

    res = rs.sync_ratings(db, CFG)

    assert res["pulled"] == 0 and res["pushed"] == 0 and res["errors"] == 0
    assert _row(db, "/data/music/A/t.mp3")["rating"] == 4


def test_getall_failure_surfaces_error(db, monkeypatch):
    def boom(url, user, pwd, page_size=500):
        raise RuntimeError("connection refused")
        yield  # pragma: no cover - never reached, keeps this a generator
    monkeypatch.setattr(rs, "iter_all_songs", boom)
    res = rs.sync_ratings(db, CFG)
    assert res["ok"] is False and "connection refused" in res["error"]


# ── star sync push-only interaction (D16) ────────────────────────────────────

@pytest.fixture
def star_remote(monkeypatch):
    class Remote:
        songs: list = []
        starred: list = []
        unstarred: list = []

        def get_starred(self, url, user, pwd):
            return list(self.songs)

        def set_star(self, url, user, pwd, sid, starred):
            (self.starred if starred else self.unstarred).append(sid)
            return True

        def resolve_id(self, url, user, pwd, track, threshold=0.80):
            return "nd9"

    r = Remote()
    monkeypatch.setattr(ss, "get_starred", r.get_starred)
    monkeypatch.setattr(ss, "set_star", r.set_star)
    monkeypatch.setattr(ss, "resolve_id", r.resolve_id)
    return r


def test_star_sync_push_only_ignores_remote_unstar(db, star_remote):
    """With rating sync on, a remote unstar is never pulled — the admin's
    derived star (rating >= 4) stays authoritative and gets pushed back out."""
    _seed(db, "/data/music/A/t.mp3", "T", "Alpha", rating=4)   # starred (derived)
    db.set_star_synced("/data/music/A/t.mp3", True, nd_song_id="nd1")
    star_remote.songs = []  # unstarred on Navidrome since

    res = ss.sync_stars(db, RATING_CFG)

    assert res["pulled"] == 0 and res["pushed"] == 1
    assert star_remote.starred == ["nd1"]  # pushed back out, not pulled
    row = next(r for r in db.all_tracks_for_star_sync() if r["file_path"] == "/data/music/A/t.mp3")
    assert row["starred"] == 1


def test_star_sync_still_merges_when_rating_sync_off(db, star_remote):
    _seed(db, "/data/music/A/t.mp3", "T", "Alpha", rating=4)
    db.set_star_synced("/data/music/A/t.mp3", True, nd_song_id="nd1")
    star_remote.songs = []  # unstarred on Navidrome since

    res = ss.sync_stars(db, CFG)  # navidrome_sync_ratings absent → normal merge

    assert res["pulled"] == 1  # pulled in as usual
    row = next(r for r in db.all_tracks_for_star_sync() if r["file_path"] == "/data/music/A/t.mp3")
    assert row["starred"] == 0


# ── periodic job gating ───────────────────────────────────────────────────────

def test_periodic_sync_skips_ratings_when_disabled(db):
    sync = ps.PeriodicSync({"navidrome_sync_ratings": False}, db)
    called = []
    sync.config = {"navidrome_sync_ratings": False}
    import bpm_tagger.integrations.rating_sync as rs_mod
    orig = rs_mod.sync_ratings
    rs_mod.sync_ratings = lambda *a, **k: called.append(True)
    try:
        sync._sync_ratings()
    finally:
        rs_mod.sync_ratings = orig
    assert called == []


def test_periodic_sync_runs_ratings_before_stars(db, monkeypatch):
    order = []
    monkeypatch.setattr(ps.PeriodicSync, "_sync_playlists", lambda self: None)
    monkeypatch.setattr(ps.PeriodicSync, "_sync_ratings", lambda self: order.append("ratings"))
    monkeypatch.setattr(ps.PeriodicSync, "_sync_stars", lambda self: order.append("stars"))
    monkeypatch.setattr(ps.PeriodicSync, "_pull_play_counts", lambda self: order.append("plays"))
    sync = ps.PeriodicSync({}, db)
    sync._tick()
    assert order == ["ratings", "stars", "plays"]


def test_periodic_sync_ratings_needs_creds(db):
    sync = ps.PeriodicSync({"navidrome_sync_ratings": True}, db)  # no url/user/pass
    called = []
    import bpm_tagger.integrations.rating_sync as rs_mod
    orig = rs_mod.sync_ratings
    rs_mod.sync_ratings = lambda *a, **k: called.append(True)
    try:
        sync._sync_ratings()
    finally:
        rs_mod.sync_ratings = orig
    assert called == []


# ── API route ────────────────────────────────────────────────────────────────

def _login(client):
    assert client.post("/api/login", json={"password": "s3cret"}).status_code == 200
    client._csrf = client.get("/api/me").get_json()["csrf_token"]


def test_sync_ratings_route_requires_csrf(client):
    _login(client)
    assert client.post("/api/settings/sync-ratings").status_code == 403


def test_sync_ratings_route_returns_counts(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(rs, "sync_ratings",
                        lambda db, cfg: {"ok": True, "remote_songs": 3, "matched": 3,
                                         "pulled": 1, "pushed": 1, "conflicts": 0, "errors": 0})
    r = client.post("/api/settings/sync-ratings", headers={"X-CSRF-Token": client._csrf})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and body["pulled"] == 1 and body["pushed"] == 1


def test_sync_ratings_route_unconfigured_is_502(client):
    _login(client)
    r = client.post("/api/settings/sync-ratings", headers={"X-CSRF-Token": client._csrf})
    assert r.status_code == 502
    assert r.get_json()["ok"] is False


def test_navidrome_settings_persist_sync_ratings_flag(client, base_config):
    _login(client)
    r = client.post("/api/settings/navidrome",
                    json={"navidrome_url": "http://nav:4533", "navidrome_user": "u",
                          "navidrome_pass": "p", "navidrome_sync_ratings": True},
                    headers={"X-CSRF-Token": client._csrf})
    assert r.status_code == 200
    s = client.get("/api/settings").get_json()["settings"]
    assert s["navidrome_sync_ratings"] is True
    import json
    from pathlib import Path
    saved = json.loads((Path(base_config["db_path"]).parent / "settings.json").read_text())
    assert saved["navidrome_sync_ratings"] is True
