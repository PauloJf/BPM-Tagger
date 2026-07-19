"""Phase 5 §8 — source-agnostic enqueue: normalized dedupe folded into enqueue_grab
and the shared enqueue_track helper."""

from bpm_tagger.db import BPMDatabase
from bpm_tagger.grabber.enqueue import enqueue_track


def _db(tmp_path):
    return BPMDatabase(str(tmp_path / "bpm.db"))


def _meta(title, artist="Artist", **kw):
    m = {"title": title, "artist": artist, "album": "Alb", "album_artist": artist,
         "duration_ms": 200000, "cover_url": "", "isrc": ""}
    m.update(kw)
    return m


# ── normalized-key dedupe for no-sid tracks ───────────────────────────────────
def test_no_sid_dedupe_blocks_second_enqueue(tmp_path):
    db = _db(tmp_path)
    first = db.enqueue_grab(_meta("Song One"))
    assert first is not None
    # Same normalized (title, artist), no spotify id → deduped.
    assert db.enqueue_grab(_meta("song  one!")) is None
    # A different track still enqueues.
    assert db.enqueue_grab(_meta("Song Two")) is not None


def test_no_sid_regrab_allowed_after_terminal(tmp_path):
    db = _db(tmp_path)
    item = db.enqueue_grab(_meta("Song One"))
    assert item is not None
    # Dedupe holds while the grab is non-terminal.
    assert db.enqueue_grab(_meta("Song One")) is None
    # Once it reaches a terminal state, the same track may be re-grabbed.
    db.transition(item, "failed")
    again = db.enqueue_grab(_meta("Song One"))
    assert again is not None and again != item


def test_sid_dedupe_still_nonterminal_only(tmp_path):
    db = _db(tmp_path)
    a = db.enqueue_grab(_meta("X", spotify_track_id="sp1"))
    assert a is not None
    assert db.enqueue_grab(_meta("X", spotify_track_id="sp1")) is None
    db.transition(a, "done")
    assert db.enqueue_grab(_meta("X", spotify_track_id="sp1")) is not None


# ── enqueue_track helper ──────────────────────────────────────────────────────
def test_enqueue_track_links_playlist_track_and_no_grabber(tmp_path):
    db = _db(tmp_path)
    item = enqueue_track(db, None, _meta("Navidrome Track"), playlist_track_id=42)
    assert item is not None
    row = db.get_grab_item(item)
    assert row["playlist_track_id"] == 42
    assert row["spotify_track_id"] in (None, "")     # no Spotify id adopted (grabber=None)
    # Second call for the same recording is deduped by the helper's fallthrough.
    assert enqueue_track(db, None, _meta("navidrome track")) is None


def test_enqueue_missing_navidrome_reflects_queued_status(tmp_path):
    """A Navidrome (no-sid) missing track queues via GrabberService.enqueue_missing and
    then reads as 'queued' on its playlist row, via the playlist_track_id link."""
    from bpm_tagger.grabber.sync_engine import GrabberService

    db = _db(tmp_path)
    pid = db.add_navidrome_playlist("nid-1", "Nav")
    db.sync_playlist_tracks(pid, [{
        "source_track_id": "song-1", "spotify_track_id": None, "position": 0,
        "title": "Missing One", "artist": "Artist", "album": "", "album_artist": "Artist",
        "duration_ms": 200000, "isrc": None, "track_no": None, "disc_no": None, "year": None,
        "cover_url": "", "added_at": "", "norm_title": "missing one", "norm_artist": "artist",
    }])
    # It has no matched local file → missing.
    assert db.get_playlist_tracks(pid, "missing")

    # A grabber with no Spotify client → no adoption; enqueue by metadata only.
    class _Tagger:
        def __init__(self, db):
            self.db, self.notifier, self.grabber = db, None, None
        def index_tags(self):
            return 0
    cfg = {"grabber_enabled": True, "db_path": db.db_path, "music_dir": str(tmp_path)}
    service = GrabberService(cfg, db, _Tagger(db), None)

    class _Disconnected:
        def is_connected(self):
            return False
    service.client = _Disconnected()

    n = service.enqueue_missing(pid)
    assert n == 1
    # The playlist row now reads 'queued' (via the grab's playlist_track_id link).
    rows = db.get_playlist_tracks(pid)
    assert rows[0]["derived_status"] == "queued"
