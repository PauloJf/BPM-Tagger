"""Phase 5 §6 — PeriodicSync scheduler: per-source gating and the disable switch."""

from bpm_tagger.integrations import periodic_sync as ps


class _FakeDB:
    def __init__(self, playlists):
        self._playlists = playlists
    def get_enabled_playlists(self):
        return self._playlists


class _FakeClient:
    def __init__(self, connected):
        self._c = connected
    def is_connected(self):
        return self._c


class _FakeSync:
    def __init__(self):
        self.synced = []
    def sync_playlist(self, pid):
        self.synced.append(pid)


class _FakeGrabber:
    def __init__(self, connected=True):
        self.client = _FakeClient(connected)
        self.sync = _FakeSync()


NAV_CFG = {"navidrome_url": "http://nav", "navidrome_user": "u", "navidrome_pass": "p"}


def _patch(monkeypatch):
    calls = {"nav": [], "stars": 0, "plays": 0}
    monkeypatch.setattr(
        "bpm_tagger.integrations.navidrome_playlists.sync_navidrome_playlist",
        lambda db, cfg, pid: calls["nav"].append(pid))

    def _stars(db, cfg):
        calls["stars"] += 1
        return {"ok": True}
    def _plays(db, cfg):
        calls["plays"] += 1
        return {"ok": True}
    monkeypatch.setattr("bpm_tagger.integrations.star_sync.sync_stars", _stars)
    monkeypatch.setattr("bpm_tagger.integrations.play_sync.pull_play_counts", _plays)
    return calls


def test_tick_syncs_by_source_and_skips_local(monkeypatch):
    calls = _patch(monkeypatch)
    db = _FakeDB([
        {"id": 1, "source": "navidrome", "name": "nav"},
        {"id": 2, "source": "spotify", "name": "sp"},
        {"id": 3, "source": "local", "name": "loc"},
    ])
    g = _FakeGrabber(connected=True)
    cfg = dict(NAV_CFG, navidrome_star_sync=True)
    ps.PeriodicSync(cfg, db, g)._tick()
    assert calls["nav"] == [1]           # navidrome synced
    assert g.sync.synced == [2]          # spotify synced via grabber
    # local never synced (id 3 absent everywhere)
    assert calls["stars"] == 1           # star sync ran (toggle on)
    assert calls["plays"] == 1           # play counts pulled (navidrome configured)


def test_tick_skips_stars_when_toggle_off(monkeypatch):
    calls = _patch(monkeypatch)
    db = _FakeDB([])
    cfg = dict(NAV_CFG)                   # navidrome_star_sync not set
    ps.PeriodicSync(cfg, db, None)._tick()
    assert calls["stars"] == 0
    assert calls["plays"] == 1           # play counts still pull (configured)


def test_tick_skips_navidrome_when_unconfigured(monkeypatch):
    calls = _patch(monkeypatch)
    db = _FakeDB([{"id": 1, "source": "navidrome", "name": "nav"}])
    ps.PeriodicSync({}, db, None)._tick()   # no creds, no toggles
    assert calls["nav"] == []            # not configured → skipped
    assert calls["stars"] == 0           # toggle off
    assert calls["plays"] == 0           # not configured → skipped


def test_tick_skips_spotify_when_disconnected(monkeypatch):
    _patch(monkeypatch)
    db = _FakeDB([{"id": 2, "source": "spotify", "name": "sp"}])
    g = _FakeGrabber(connected=False)
    ps.PeriodicSync({}, db, g)._tick()
    assert g.sync.synced == []           # no live Spotify connection → skipped


def test_disabled_interval_is_noop(monkeypatch):
    calls = _patch(monkeypatch)
    called = {"tick": False}
    t = ps.PeriodicSync({"sync_interval_minutes": 0}, _FakeDB([]), None)
    monkeypatch.setattr(t, "_tick", lambda: called.__setitem__("tick", True))
    t.run()                              # returns immediately, no tick
    assert called["tick"] is False
    assert calls == {"nav": [], "stars": 0, "plays": 0}
