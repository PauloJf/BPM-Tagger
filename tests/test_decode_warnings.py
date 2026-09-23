"""Decode-problem tracking: detect_bpm() records what went wrong decoding a
file (empty analysis windows, a fully undecodable file, a detector-level
AudioLoadError), the DB persists and filters on it, and the API exposes it as
a parsed list rather than raw JSON text.

Real incident this covers: Anyma - Neverland (From Japan).m4a scored a
perfectly plausible 128.1 BPM off 2 bad windows out of 3 — nothing in the UI
hinted the analysis was degraded.
"""

import json
import os
import sqlite3

import pytest

import bpm_tagger.bpm.pipeline as pipeline
from bpm_tagger import detect_bpm
from bpm_tagger.bpm.audio import AudioLoadError
from bpm_tagger.db import BPMDatabase


def cfg(**over):
    base = {
        "bpm_min": 60.0,
        "bpm_max": 200.0,
        "octave_correction": True,
        "review_disagree_threshold": 15.0,
        "use_deeprhythm": False,
        "use_essentia": False,
        "multi_segment": True,
        "multi_segment_count": 3,
        "segment_duration": 45.0,
    }
    base.update(over)
    return base


# ── db: migration ────────────────────────────────────────────────────────────

def test_migration_adds_decode_warnings_to_an_existing_db(tmp_path):
    """An older DB (pre-decode-warnings) must gain the column additively, with
    existing rows left NULL rather than erroring."""
    db_path = str(tmp_path / "old.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE tracks (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path      TEXT UNIQUE NOT NULL,
            status         TEXT DEFAULT 'pending',
            error_message  TEXT,
            needs_review   INTEGER DEFAULT 0,
            reviewed       INTEGER DEFAULT 0,
            locked         INTEGER DEFAULT 0
        )
    """)
    conn.execute("INSERT INTO tracks (file_path, status) VALUES ('/m/0.mp3', 'done')")
    conn.commit()
    conn.close()

    db = BPMDatabase(db_path)
    row = db.get_track("/m/0.mp3")
    assert "decode_warnings" in row
    assert row["decode_warnings"] is None


# ── db: upsert round-trip ────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    return BPMDatabase(str(tmp_path / "bpm.db"))


def _upsert(db, path, **kw):
    db.upsert_track(path, "1:1", 128.0, None, None, None, 0.9, "librosa", "done", **kw)


def test_upsert_round_trips_decode_warnings(db):
    payload = json.dumps([{"code": "empty_windows", "detail": "1 of 3 analysis windows decoded to nothing"}])
    _upsert(db, "/music/a.mp3", decode_warnings=payload)
    row = db.get_track("/music/a.mp3")
    assert row["decode_warnings"] == payload


def test_upsert_defaults_to_none(db):
    """Existing callers that don't know about decode_warnings must keep working."""
    _upsert(db, "/music/a.mp3")
    assert db.get_track("/music/a.mp3")["decode_warnings"] is None


def test_a_clean_rescan_clears_a_previous_warning(db):
    """Unlike waveform/loudness, decode_warnings reflects the latest pass — a
    rescan that decodes cleanly must not leave a stale warning behind."""
    _upsert(db, "/music/a.mp3", decode_warnings=json.dumps([{"code": "empty_windows", "detail": "x"}]))
    _upsert(db, "/music/a.mp3")  # clean pass, no decode_warnings passed
    assert db.get_track("/music/a.mp3")["decode_warnings"] is None


# ── db: "problems" filter ────────────────────────────────────────────────────

def test_problems_filter_matches_only_flagged_tracks(db):
    _upsert(db, "/music/bad.mp3", decode_warnings=json.dumps([{"code": "empty_windows", "detail": "x"}]))
    _upsert(db, "/music/clean.mp3")
    rows, total = db.get_tracks_page("", 50, 0, filter="problems")
    assert total == 1
    assert [r["file_path"] for r in rows] == ["/music/bad.mp3"]


def test_problems_filter_excludes_empty_array_and_empty_string(db):
    """'[]' and '' are not "clean" in the sense of never having been analyzed,
    but they must not be treated as a decode problem either."""
    _upsert(db, "/music/empty_array.mp3", decode_warnings="[]")
    _upsert(db, "/music/empty_string.mp3", decode_warnings="")
    _upsert(db, "/music/none.mp3")
    _, total = db.get_tracks_page("", 50, 0, filter="problems")
    assert total == 0


def test_problems_filter_also_works_on_track_paths(db):
    _upsert(db, "/music/bad.mp3", decode_warnings=json.dumps([{"code": "decode_failed", "detail": "x"}]))
    _upsert(db, "/music/clean.mp3")
    rows = db.get_track_paths(filter="problems")
    assert [r["file_path"] for r in rows] == ["/music/bad.mp3"]


# ── db: stats count ──────────────────────────────────────────────────────────

def test_stats_counts_decode_problems(db):
    _upsert(db, "/music/bad.mp3", decode_warnings=json.dumps([{"code": "empty_windows", "detail": "x"}]))
    _upsert(db, "/music/clean.mp3")
    assert db.get_stats()["decode_problems"] == 1


def test_stats_excludes_deleted_tracks_from_decode_problems(db):
    db.upsert_track("/music/gone.mp3", "1:1", None, None, None, None, None, None, "deleted",
                    decode_warnings=json.dumps([{"code": "empty_windows", "detail": "x"}]))
    assert db.get_stats()["decode_problems"] == 0


# ── pipeline: warning generation ─────────────────────────────────────────────

def _stub_librosa_multiseg(monkeypatch, result):
    monkeypatch.setattr(pipeline, "_detect_bpm_librosa_multiseg", lambda *a, **kw: result)


def test_empty_windows_warning(monkeypatch):
    """Some but not all windows empty -> empty_windows, with the exact counts."""
    _stub_librosa_multiseg(monkeypatch, (129.0, 0.9, 3, 1))
    result = detect_bpm("/music/x.m4a", cfg())
    assert result["decode_warnings"] == [
        {"code": "empty_windows", "detail": "1 of 3 analysis windows decoded to nothing"}
    ]


def test_no_decodable_audio_warning(monkeypatch):
    """Every window empty -> no_decodable_audio, not empty_windows."""
    _stub_librosa_multiseg(monkeypatch, (0.0, 0.0, 3, 3))
    result = detect_bpm("/music/x.m4a", cfg())
    assert result["decode_warnings"] == [
        {"code": "no_decodable_audio", "detail": "no analysis window produced audio"}
    ]


def test_no_windows_empty_means_no_warning(monkeypatch):
    _stub_librosa_multiseg(monkeypatch, (128.0, 0.9, 3, 0))
    result = detect_bpm("/music/x.m4a", cfg())
    assert result["decode_warnings"] == []


def test_single_window_mode_reports_no_decodable_audio(monkeypatch):
    """multi_segment=False path: one window, empty -> still no_decodable_audio,
    not empty_windows (there's nothing "some" about a single window)."""
    # pipeline.py imports _librosa_window by name, so the patch must target
    # its own module namespace, not bpm.detectors's.
    monkeypatch.setattr(pipeline, "_librosa_window", lambda p, o, d: (0.0, 0.0))
    result = detect_bpm("/music/x.m4a", cfg(multi_segment=False))
    assert result["decode_warnings"] == [
        {"code": "no_decodable_audio", "detail": "no analysis window produced audio"}
    ]


def test_deeprhythm_decode_failure_is_recorded(monkeypatch):
    def boom(path):
        raise AudioLoadError("ffmpeg could not decode it: some codec error")
    monkeypatch.setattr(pipeline, "_detect_bpm_deeprhythm", boom)
    _stub_librosa_multiseg(monkeypatch, (128.0, 0.9, 3, 0))
    result = detect_bpm("/music/x.m4a", cfg(use_deeprhythm=True))
    assert result["decode_warnings"] == [
        {"code": "decode_failed",
         "detail": "deeprhythm could not decode the file: ffmpeg could not decode it: some codec error"}
    ]
    assert result["bpm_dr"] is None


def test_other_deeprhythm_exceptions_are_not_recorded_as_decode_warnings(monkeypatch):
    """Only AudioLoadError is a decode problem — any other failure (a bug, an
    OOM, ...) is logged as before but must not masquerade as one."""
    def boom(path):
        raise RuntimeError("boom")
    monkeypatch.setattr(pipeline, "_detect_bpm_deeprhythm", boom)
    _stub_librosa_multiseg(monkeypatch, (128.0, 0.9, 3, 0))
    result = detect_bpm("/music/x.m4a", cfg(use_deeprhythm=True))
    assert result["decode_warnings"] == []


def test_essentia_failures_never_produce_a_decode_warning(monkeypatch):
    """_detect_bpm_essentia swallows its own exceptions and returns None on any
    failure — decode or otherwise — so it must never contribute a warning."""
    monkeypatch.setattr(pipeline, "_detect_bpm_essentia", lambda path: None)
    _stub_librosa_multiseg(monkeypatch, (128.0, 0.9, 3, 0))
    result = detect_bpm("/music/x.m4a", cfg(use_essentia=True))
    assert result["decode_warnings"] == []


# ── web API ──────────────────────────────────────────────────────────────────

def _app(base_config):
    from bpm_tagger.web.app import create_app
    return create_app(base_config)


def _login(client, password="s3cret"):
    client.post("/api/login", json={"password": password})
    return {"X-CSRF-Token": client.get("/api/me").get_json()["csrf_token"]}


def test_api_track_exposes_decode_warnings_as_a_parsed_list(base_config):
    app = _app(base_config)
    client = app.test_client()
    _login(client)
    path = os.path.join(base_config["music_dir"], "a.mp3")
    from bpm_tagger.web.state import state
    with app.app_context():
        _upsert(state().db, path,
               decode_warnings=json.dumps([{"code": "empty_windows", "detail": "x"}]))
    r = client.get("/api/track", query_string={"path": path})
    assert r.status_code == 200
    body = r.get_json()
    assert body["track"]["decode_warnings"] == [{"code": "empty_windows", "detail": "x"}]


def test_api_track_reports_empty_list_when_clean(base_config):
    app = _app(base_config)
    client = app.test_client()
    _login(client)
    path = os.path.join(base_config["music_dir"], "a.mp3")
    from bpm_tagger.web.state import state
    with app.app_context():
        _upsert(state().db, path)
    body = client.get("/api/track", query_string={"path": path}).get_json()
    assert body["track"]["decode_warnings"] == []


def test_api_tracks_exposes_parsed_list_and_problems_count(base_config):
    app = _app(base_config)
    client = app.test_client()
    _login(client)
    from bpm_tagger.web.state import state
    with app.app_context():
        _upsert(state().db, "/music/bad.mp3",
               decode_warnings=json.dumps([{"code": "decode_failed", "detail": "x"}]))
        _upsert(state().db, "/music/clean.mp3")
    body = client.get("/api/tracks").get_json()
    assert body["problems_count"] == 1
    by_path = {t["file_path"]: t for t in body["tracks"]}
    assert by_path["/music/bad.mp3"]["decode_warnings"] == [{"code": "decode_failed", "detail": "x"}]
    assert by_path["/music/clean.mp3"]["decode_warnings"] == []


def test_api_tracks_problems_filter(base_config):
    app = _app(base_config)
    client = app.test_client()
    _login(client)
    from bpm_tagger.web.state import state
    with app.app_context():
        _upsert(state().db, "/music/bad.mp3",
               decode_warnings=json.dumps([{"code": "empty_windows", "detail": "x"}]))
        _upsert(state().db, "/music/clean.mp3")
    body = client.get("/api/tracks", query_string={"filter": "problems"}).get_json()
    assert body["total"] == 1
    assert body["tracks"][0]["file_path"] == "/music/bad.mp3"


def test_api_tracks_paths_problems_filter(base_config):
    app = _app(base_config)
    client = app.test_client()
    _login(client)
    from bpm_tagger.web.state import state
    with app.app_context():
        _upsert(state().db, "/music/bad.mp3",
               decode_warnings=json.dumps([{"code": "empty_windows", "detail": "x"}]))
        _upsert(state().db, "/music/clean.mp3")
    body = client.get("/api/tracks/paths", query_string={"filter": "problems"}).get_json()
    assert [t["file_path"] for t in body["tracks"]] == ["/music/bad.mp3"]


def test_api_stats_exposes_decode_problems_count(base_config):
    app = _app(base_config)
    client = app.test_client()
    _login(client)
    from bpm_tagger.web.state import state
    with app.app_context():
        _upsert(state().db, "/music/bad.mp3",
               decode_warnings=json.dumps([{"code": "no_decodable_audio", "detail": "x"}]))
    body = client.get("/api/stats").get_json()
    assert body["summary"]["decode_problems"] == 1


# Every endpoint that serialises a full `SELECT *` track row has to parse the
# column, not just the library list and the detail page. /api/artist and
# /api/album were handing back the raw JSON string under a field the SPA types
# as a list — harmless only because nothing rendered it there yet.

def _seed_with_warning(app, base_config, name, **cols):
    from bpm_tagger.web.state import state
    path = os.path.join(base_config["music_dir"], name)
    with app.app_context():
        _upsert(state().db, path,
                decode_warnings=json.dumps([{"code": "empty_windows", "detail": "x"}]))
        state().db.update_track_metadata(path, path, {
            "title": "T", "artist": "Artist A", "album": "Album A",
            "album_artist": "Artist A", **cols}, "0:0")
    return path


def test_api_artist_exposes_decode_warnings_as_a_parsed_list(base_config):
    app = _app(base_config)
    client = app.test_client()
    _login(client)
    _seed_with_warning(app, base_config, "a.mp3")
    rows = client.get("/api/artist", query_string={"name": "Artist A"}).get_json()["tracks"]
    assert rows, "expected the seeded track back"
    assert rows[0]["decode_warnings"] == [{"code": "empty_windows", "detail": "x"}]


def test_api_album_exposes_decode_warnings_as_a_parsed_list(base_config):
    app = _app(base_config)
    client = app.test_client()
    _login(client)
    _seed_with_warning(app, base_config, "b.mp3")
    rows = client.get("/api/album", query_string={"album": "Album A"}).get_json()["tracks"]
    assert rows, "expected the seeded track back"
    assert rows[0]["decode_warnings"] == [{"code": "empty_windows", "detail": "x"}]
