"""The sidebar's background-activity feed: the watcher's read-only batch, the sync
registry, and /api/activity (loudness/waveform/lyrics/ISRC fills + syncs)."""

import threading

from bpm_tagger import activity
from bpm_tagger.bpm.pipeline import ScanProgress
from bpm_tagger.scan.watcher import WatchHandler
from bpm_tagger.web.api import loudness, tracks


# ── ScanProgress batch (watch mode) ──────────────────────────────────────────
def test_batch_is_reported_without_a_scan():
    p = ScanProgress()
    assert p.snapshot()["watching"] is False
    p.begin_batch(3)
    p.batch_start_file("/music/a/Song One.mp3")
    snap = p.snapshot()
    assert snap["watching"] is True and snap["is_scanning"] is False   # no Pause/Stop for it
    assert (snap["batch_done"], snap["batch_total"], snap["batch_file"]) == (0, 3, "Song One.mp3")
    p.batch_finish_file()
    assert p.snapshot()["batch_done"] == 1 and p.snapshot()["batch_file"] == ""
    p.end_batch()
    assert p.snapshot()["watching"] is False


def test_a_scan_starting_does_not_clear_the_batch():
    p = ScanProgress()
    p.begin_batch(2)
    p.start(10)            # resets the scan fields only
    assert p.snapshot()["watching"] is True and p.snapshot()["batch_total"] == 2


class _Tagger:
    def __init__(self, fail_on=None):
        self.progress = ScanProgress()
        self._stop_event = threading.Event()
        self._pause_event = threading.Event(); self._pause_event.set()
        self.seen = []
        self.during = []
        self._fail_on = fail_on

    def process_file(self, path, force=False):
        self.seen.append(path)
        self.during.append(self.progress.snapshot())
        if path == self._fail_on:
            raise RuntimeError("boom")
        return {"status": "tagged" if path.endswith(".mp3") else "skipped"}


def test_watcher_batch_reports_progress_and_ends():
    t = _Tagger()
    handler = WatchHandler(t)
    assert handler._process_batch(["/m/a.mp3", "/m/b.txt"]) is True
    assert [(s["batch_done"], s["batch_total"], s["batch_file"]) for s in t.during] == \
        [(0, 2, "a.mp3"), (1, 2, "b.txt")]
    assert t.progress.snapshot()["watching"] is False


def test_watcher_batch_clears_even_when_a_file_raises():
    t = _Tagger(fail_on="/m/a.mp3")
    handler = WatchHandler(t)
    try:
        handler._process_batch(["/m/a.mp3"])
    except RuntimeError:
        pass
    assert t.progress.snapshot()["watching"] is False


def test_empty_batch_never_shows_as_watching():
    t = _Tagger()
    WatchHandler(t)._process_batch([])
    assert t.progress.snapshot()["watching"] is False


# ── registry ─────────────────────────────────────────────────────────────────
def test_track_lists_work_until_the_last_holder_leaves():
    assert activity.snapshot() == []
    with activity.track("sync", "Syncing with Navidrome"):
        with activity.track("sync", "Syncing with Navidrome"):
            assert [a["id"] for a in activity.snapshot()] == ["sync"]
        assert [a["id"] for a in activity.snapshot()] == ["sync"]   # outer still running
    assert activity.snapshot() == []


def test_track_clears_when_the_work_raises():
    try:
        with activity.track("sync", "x"):
            raise ValueError
    except ValueError:
        pass
    assert activity.snapshot() == []


# ── endpoint ─────────────────────────────────────────────────────────────────
def _login(client):
    assert client.post("/api/login", json={"password": "s3cret"}).status_code == 200


def test_activity_requires_login(client):
    assert client.get("/api/activity").status_code == 401


def test_activity_is_empty_when_nothing_runs(client):
    _login(client)
    assert client.get("/api/activity").get_json() == {"items": []}


def test_activity_lists_running_fills_and_syncs(client, monkeypatch):
    _login(client)
    monkeypatch.setitem(loudness._fill, "running", True)
    monkeypatch.setitem(loudness._fill, "total", 900)
    monkeypatch.setitem(loudness._fill, "done", 120)
    monkeypatch.setitem(tracks._isrc_fill, "running", True)   # total not known yet
    monkeypatch.setitem(tracks._isrc_fill, "total", 0)
    with activity.track("sync", "Syncing with Navidrome"):
        items = {i["id"]: i for i in client.get("/api/activity").get_json()["items"]}
    assert items["loudness"]["done"] == 120 and items["loudness"]["total"] == 900
    assert items["isrc"]["total"] is None                       # indeterminate: no bar
    assert items["sync"]["label"] == "Syncing with Navidrome"
    assert "waveform" not in items and "lyrics" not in items


def test_activity_is_not_available_to_a_player(base_config):
    from tests.test_api_player import _app, _client, _login
    client = _client(_app(base_config, run_password="guest"))
    resp, _ = _login(client, "guest")
    assert resp.status_code == 200
    assert client.get("/api/activity").status_code == 403
