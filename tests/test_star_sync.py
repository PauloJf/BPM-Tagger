"""Navidrome two-way star sync: merge truth table, driver, and API route.

The driver tests monkeypatch the Subsonic client functions in the star_sync
module namespace and run against a real temp BPMDatabase, per the plan
(docs/plans/navidrome-star-sync.md § Testing).
"""


import pytest

from bpm_tagger.db import BPMDatabase
from bpm_tagger.grabber import matching as m
from bpm_tagger.integrations import star_sync as ss

# ── merge_star: the pure three-way merge ─────────────────────────────────────

@pytest.mark.parametrize("local,remote,base,final,action", [
    # agreement → nothing, regardless of base
    (False, False, False, False, "none"),
    (False, False, True,  False, "none"),
    (True,  True,  False, True,  "none"),
    (True,  True,  True,  True,  "none"),
    # local changed, remote didn't → push local outward
    (True,  False, False, True,  "push"),   # starred here since last sync
    (False, True,  True,  False, "push"),   # unstarred here since last sync
    # remote changed, local didn't → pull remote in
    (True,  False, True,  False, "pull"),   # unstarred in Navidrome
    (False, True,  False, True,  "pull"),   # starred in Navidrome
])
def test_merge_star_truth_table(local, remote, base, final, action):
    assert ss.merge_star(local, remote, base) == (final, action)


def test_merge_star_conflict_is_unreachable_for_booleans():
    """local != remote forces base to equal one side, so exactly one side
    'changed' — every disagreement resolves as push or pull, never conflict."""
    for local, remote, base in [(a, b, c) for a in (0, 1) for b in (0, 1) for c in (0, 1)]:
        _, action = ss.merge_star(bool(local), bool(remote), bool(base))
        assert action != "conflict"


# ── driver fixtures ──────────────────────────────────────────────────────────

CFG = {"navidrome_url": "http://nav:4533", "navidrome_user": "u", "navidrome_pass": "p"}


def _seed(db, path, title, artist, starred=False, duration_ms=200000):
    db.upsert_track(path, "1:1", 120.0, None, None, 120.0, 0.9, "librosa", "done")
    db.update_track_tags(path, {
        "title": title, "artist": artist, "album": "Alb", "album_artist": artist,
        "track_no": 1, "disc_no": 1, "year": 2020, "isrc": "", "duration_ms": duration_ms,
        "norm_title": m.normalize_title(title), "norm_artist": m.normalize_artist(artist),
    }, "1:1")
    if starred:
        db.set_starred(path, True)


def _song(sid, path, title="T", artist="A", duration_s=200):
    return {"id": sid, "path": path, "title": title, "artist": artist,
            "album": "Alb", "duration": duration_s}


@pytest.fixture
def db(tmp_path):
    return BPMDatabase(str(tmp_path / "s.db"))


@pytest.fixture
def remote(monkeypatch):
    """Fake Subsonic server state: .songs is the starred set; .starred/.unstarred
    record writes; .fail makes every set_star call fail."""
    class Remote:
        songs: list = []
        starred: list = []
        unstarred: list = []
        fail = False
        resolve: dict = {}      # (artist, title) -> id for search3 resolution

        def get_starred(self, url, user, pwd):
            return list(self.songs)

        def set_star(self, url, user, pwd, sid, starred):
            if self.fail:
                return False
            (self.starred if starred else self.unstarred).append(sid)
            return True

        def resolve_id(self, url, user, pwd, track, threshold=0.80):
            return self.resolve.get((track.get("artist"), track.get("title")))

    r = Remote()
    monkeypatch.setattr(ss, "get_starred", r.get_starred)
    monkeypatch.setattr(ss, "set_star", r.set_star)
    monkeypatch.setattr(ss, "resolve_id", r.resolve_id)
    return r


def _row(db, path):
    return next(r for r in db.all_tracks_for_star_sync() if r["file_path"] == path)


# ── driver behavior ──────────────────────────────────────────────────────────

def test_unconfigured_returns_error(db):
    res = ss.sync_stars(db, {})
    assert res["ok"] is False and "configured" in res["error"]


def test_first_run_bootstrap_pulls_and_pushes(db, remote):
    _seed(db, "/data/music/A/local_star.mp3", "Local Star", "Alpha", starred=True)
    _seed(db, "/data/music/A/remote_star.mp3", "Remote Star", "Alpha")
    _seed(db, "/data/music/A/plain.mp3", "Plain", "Alpha")
    remote.songs = [_song("nd1", "/music/A/remote_star.mp3", "Remote Star", "Alpha")]
    remote.resolve[("Alpha", "Local Star")] = "nd9"

    res = ss.sync_stars(db, CFG)

    assert res["ok"] and res["pushed"] == 1 and res["pulled"] == 1 and res["conflicts"] == 0
    assert remote.starred == ["nd9"]                       # local star pushed out
    pulled = _row(db, "/data/music/A/remote_star.mp3")
    assert pulled["starred"] == 1 and pulled["starred_base"] == 1 and pulled["nd_song_id"] == "nd1"
    pushed = _row(db, "/data/music/A/local_star.mp3")
    assert pushed["starred"] == 1 and pushed["starred_base"] == 1 and pushed["nd_song_id"] == "nd9"
    plain = _row(db, "/data/music/A/plain.mp3")
    assert plain["starred"] == 0 and plain["starred_base"] == 0


def test_pull_remote_unstar(db, remote):
    _seed(db, "/data/music/A/t.mp3", "T", "Alpha", starred=True)
    db.set_star_synced("/data/music/A/t.mp3", True, nd_song_id="nd1")  # base=1, synced before
    remote.songs = []                                       # unstarred on Navidrome since

    res = ss.sync_stars(db, CFG)

    assert res["pulled"] == 1 and res["pushed"] == 0
    row = _row(db, "/data/music/A/t.mp3")
    assert row["starred"] == 0 and row["starred_base"] == 0
    assert remote.starred == [] and remote.unstarred == []  # no remote write on a pull


def test_push_local_unstar(db, remote):
    _seed(db, "/data/music/A/t.mp3", "T", "Alpha")
    db.set_star_synced("/data/music/A/t.mp3", True, nd_song_id="nd1")  # last sync: starred
    db.set_starred("/data/music/A/t.mp3", False)            # unstarred locally since
    remote.songs = [_song("nd1", "/music/A/t.mp3", "T", "Alpha")]

    res = ss.sync_stars(db, CFG)

    assert res["pushed"] == 1
    assert remote.unstarred == ["nd1"]
    row = _row(db, "/data/music/A/t.mp3")
    assert row["starred"] == 0 and row["starred_base"] == 0


def test_failed_remote_write_keeps_baseline_for_retry(db, remote):
    _seed(db, "/data/music/A/t.mp3", "T", "Alpha", starred=True)
    remote.resolve[("Alpha", "T")] = "nd1"
    remote.fail = True

    res = ss.sync_stars(db, CFG)

    assert res["failed"] == 1 and res["pushed"] == 0
    row = _row(db, "/data/music/A/t.mp3")
    assert row["starred"] == 1 and row["starred_base"] == 0  # untouched → retried next run
    remote.fail = False
    res2 = ss.sync_stars(db, CFG)
    assert res2["pushed"] == 1 and remote.starred == ["nd1"]


def test_unresolvable_push_counts_failed(db, remote):
    _seed(db, "/data/music/A/t.mp3", "T", "Alpha", starred=True)
    remote.resolve = {}                                     # search3 finds nothing

    res = ss.sync_stars(db, CFG)

    assert res["failed"] == 1
    assert _row(db, "/data/music/A/t.mp3")["starred_base"] == 0


def test_unmatched_remote_star_is_counted_not_fatal(db, remote):
    _seed(db, "/data/music/A/t.mp3", "T", "Alpha")
    remote.songs = [_song("ndX", "/elsewhere/unknown.mp3", "Nothing Like It", "Nobody")]

    res = ss.sync_stars(db, CFG)

    assert res["ok"] and res["unmatched_remote"] == 1 and res["pulled"] == 0


def test_fuzzy_fallback_claims_pathless_remote_star(db, remote):
    # Path roots don't line up at all — metadata does (title+artist+duration).
    _seed(db, "/data/music/A/song.mp3", "Harder Better", "Alpha", duration_ms=200000)
    remote.songs = [_song("nd7", "/totally/different/root.flac", "Harder Better", "Alpha")]

    res = ss.sync_stars(db, CFG)

    assert res["pulled"] == 1 and res["unmatched_remote"] == 0
    row = _row(db, "/data/music/A/song.mp3")
    assert row["starred"] == 1 and row["nd_song_id"] == "nd7"


def test_getstarred_failure_surfaces_error(db, monkeypatch):
    def boom(url, user, pwd):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(ss, "get_starred", boom)
    res = ss.sync_stars(db, CFG)
    assert res["ok"] is False and "connection refused" in res["error"]


def test_deleted_rows_are_excluded(db, remote):
    _seed(db, "/data/music/A/gone.mp3", "Gone", "Alpha", starred=True)
    db.mark_deleted("/data/music/A/gone.mp3")
    res = ss.sync_stars(db, CFG)
    assert res["checked"] == 0 and res["failed"] == 0


# ── API route ────────────────────────────────────────────────────────────────

def _login(client):
    assert client.post("/api/login", json={"password": "s3cret"}).status_code == 200
    client._csrf = client.get("/api/me").get_json()["csrf_token"]


def test_sync_stars_route_requires_csrf(client):
    _login(client)
    assert client.post("/api/settings/sync-stars").status_code == 403


def test_sync_stars_route_returns_counts(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(ss, "sync_stars",
                        lambda db, cfg: {"ok": True, "checked": 3, "pushed": 1, "pulled": 2,
                                         "conflicts": 0, "unmatched_remote": 0, "failed": 0})
    r = client.post("/api/settings/sync-stars", headers={"X-CSRF-Token": client._csrf})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and body["pushed"] == 1 and body["pulled"] == 2


def test_sync_stars_route_unconfigured_is_502(client):
    _login(client)
    r = client.post("/api/settings/sync-stars", headers={"X-CSRF-Token": client._csrf})
    assert r.status_code == 502
    assert r.get_json()["ok"] is False


def test_navidrome_settings_persist_star_sync_flag(client, base_config):
    _login(client)
    r = client.post("/api/settings/navidrome",
                    json={"navidrome_url": "http://nav:4533", "navidrome_user": "u",
                          "navidrome_pass": "p", "navidrome_star_sync": True},
                    headers={"X-CSRF-Token": client._csrf})
    assert r.status_code == 200
    s = client.get("/api/settings").get_json()["settings"]
    assert s["navidrome_star_sync"] is True
    # Persisted to settings.json alongside the other Navidrome keys.
    import json
    from pathlib import Path
    saved = json.loads((Path(base_config["db_path"]).parent / "settings.json").read_text())
    assert saved["navidrome_star_sync"] is True


def test_sync_stars_route_monkeypatch_targets_module_used_by_route(client, monkeypatch):
    """Guard against the route importing sync_stars in a way a module-level
    monkeypatch wouldn't see (it imports inside the handler, from the module)."""
    _login(client)
    called = {}
    def fake(db, cfg):
        called["yes"] = True
        return {"ok": True, "checked": 0, "pushed": 0, "pulled": 0,
                "conflicts": 0, "unmatched_remote": 0, "failed": 0}
    monkeypatch.setattr(ss, "sync_stars", fake)
    client.post("/api/settings/sync-stars", headers={"X-CSRF-Token": client._csrf})
    assert called.get("yes") is True
