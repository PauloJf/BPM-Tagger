"""GrabPipeline: download→transcode→tag→BPM→managed, + watcher anti-loop."""

import os

import pytest

from bpm_tagger.db import BPMDatabase
from bpm_tagger.grabber.providers.base import DownloadedFile, Provider, ProviderCandidate
from bpm_tagger.grabber import worker as worker_mod
from bpm_tagger.grabber.worker import GrabPipeline


class FakeProvider(Provider):
    name = "fake"
    lossless = True

    def __init__(self, quality_match=True):
        self.quality_match = quality_match

    def search(self, meta, limit=8):
        if self.quality_match:
            # Identical metadata → score 1.0 (auto-accept).
            return [ProviderCandidate(provider="fake", provider_track_id="1",
                    title=meta.title, artist=meta.artist, album=meta.album,
                    duration_ms=meta.duration_ms, isrc=meta.isrc, quality="LOSSLESS")]
        # Poor match → below auto-accept.
        return [ProviderCandidate(provider="fake", provider_track_id="9",
                title="Something Unrelated", artist="Other Person",
                duration_ms=99000, quality="LOW")]

    def download(self, cand, dest_dir, progress_cb=None):
        p = os.path.join(dest_dir, "src.mp3")
        with open(p, "wb") as fh:
            fh.write(b"\x00" * 256)
        if progress_cb:
            progress_cb(1.0)
        return DownloadedFile(path=p, ext="mp3", provider="fake")


def _config(tmp_path, **over):
    cfg = {
        "db_path": str(tmp_path / "data" / "bpm.db"),
        "music_dir": str(tmp_path / "music"),
        "output_format": "mp3-320",
        "path_template": "{AlbumArtist}/{Album}/{TrackNo:02d} - {Title}.{ext}",
        "write_tags": False,          # skip real BPM tag write on the fake file
        "navidrome_url": "",
        "auto_accept_threshold": 0.85,
        "ask_threshold": 0.55,
    }
    cfg.update(over)
    os.makedirs(cfg["music_dir"], exist_ok=True)
    os.makedirs(os.path.dirname(cfg["db_path"]), exist_ok=True)
    return cfg


@pytest.fixture(autouse=True)
def _stub_bpm(monkeypatch):
    monkeypatch.setattr(worker_mod, "detect_bpm", lambda path, config: {
        "bpm": 128.0, "bpm_dr": None, "bpm_es": None, "bpm_lb": 128.0,
        "confidence": 0.9, "detector": "librosa", "needs_review": False})


def _enqueue(db):
    return db.enqueue_grab({
        "spotify_track_id": "s1", "title": "Blinding Lights", "artist": "The Weeknd",
        "album": "After Hours", "album_artist": "The Weeknd", "duration_ms": 200000,
        "isrc": "USUG11904206", "track_no": 3, "disc_no": 1, "year": 2020, "cover_url": ""})


def test_pipeline_happy_path_creates_managed_track(tmp_path):
    cfg = _config(tmp_path)
    db = BPMDatabase(cfg["db_path"])
    item_id = _enqueue(db)
    pipe = GrabPipeline(cfg, db, tagger=None, providers=[FakeProvider()])

    status = pipe.process_item(db.get_grab_item(item_id))
    assert status == "done"

    row = db.get_grab_item(item_id)
    assert row["status"] == "done"
    final = row["final_path"]
    assert final and os.path.exists(final)
    # Rendered to the template, in the target format, under music_dir.
    assert final.replace("\\", "/").endswith("The Weeknd/After Hours/03 - Blinding Lights.mp3")

    tr = db.get_track(final)
    assert tr is not None
    assert tr["managed"] == 1
    assert tr["bpm"] == 128.0
    assert tr["spotify_track_id"] == "s1"
    assert tr["status"] == "done"


def test_watcher_does_not_reanalyze_managed_download(tmp_path):
    cfg = _config(tmp_path)
    db = BPMDatabase(cfg["db_path"])
    item_id = _enqueue(db)
    GrabPipeline(cfg, db, tagger=None, providers=[FakeProvider()]).process_item(db.get_grab_item(item_id))

    final = db.get_grab_item(item_id)["final_path"]
    from bpm_tagger.bpm.tags import get_file_hash
    # The DB hash was taken AFTER the (skipped) BPM write, so the file is unchanged
    # → the watcher/scanner must NOT re-analyze it (plan risk #4).
    assert db.needs_analysis(final, get_file_hash(final)) is False


def test_grab_events_recorded(tmp_path):
    cfg = _config(tmp_path)
    db = BPMDatabase(cfg["db_path"])
    item_id = _enqueue(db)
    GrabPipeline(cfg, db, tagger=None, providers=[FakeProvider()]).process_item(db.get_grab_item(item_id))
    events = [e["event"] for e in db.get_grab_events(item_id)]
    assert "searching" in events and "downloading" in events and "done" in events


def test_dry_run_stops_at_awaiting_user(tmp_path):
    cfg = _config(tmp_path, grab_dry_run=True)
    db = BPMDatabase(cfg["db_path"])
    item_id = _enqueue(db)
    status = GrabPipeline(cfg, db, tagger=None, providers=[FakeProvider()]).process_item(db.get_grab_item(item_id))
    assert status == "awaiting_user"
    assert db.get_grab_item(item_id)["final_path"] is None


def test_low_score_routes_to_awaiting_user(tmp_path):
    cfg = _config(tmp_path)
    db = BPMDatabase(cfg["db_path"])
    item_id = _enqueue(db)
    status = GrabPipeline(cfg, db, tagger=None,
                          providers=[FakeProvider(quality_match=False)]).process_item(db.get_grab_item(item_id))
    assert status == "awaiting_user"
    # Candidates are still recorded for the inbox (M5).
    assert len(db.get_grab_candidates(item_id)) == 1
