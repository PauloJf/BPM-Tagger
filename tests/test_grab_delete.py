"""delete_grab(): removing a queue item also removes its candidates and events.

The Delete action on the queue page (failed/skipped rows) drops the bookkeeping
row and its children in one transaction — explicitly, so it's correct on older
databases that predate ON DELETE CASCADE.
"""

from bpm_tagger.db import BPMDatabase
from bpm_tagger.grabber import matching as m


def _db(tmp_path):
    return BPMDatabase(str(tmp_path / "bpm.db"))


def _enqueue(db, title="song"):
    return db.enqueue_grab({"title": title, "artist": "A", "spotify_track_id": f"sp_{title}"})


def test_delete_grab_removes_item_and_children(tmp_path):
    db = _db(tmp_path)
    item_id = _enqueue(db)
    db.add_grab_candidates(item_id, [{"provider": "deezer", "title": "song", "rank": 0}])
    db.add_grab_event(item_id, "failed", "no provider match")
    assert db.get_grab_item(item_id) is not None
    assert db.get_grab_candidates(item_id)
    assert db.get_grab_events(item_id)

    assert db.delete_grab(item_id) is True

    assert db.get_grab_item(item_id) is None
    assert db.get_grab_candidates(item_id) == []
    assert db.get_grab_events(item_id) == []


def test_delete_grab_missing_returns_false(tmp_path):
    db = _db(tmp_path)
    assert db.delete_grab(999999) is False


def test_delete_completed_grabs_only_removes_done(tmp_path):
    db = _db(tmp_path)
    d, f = _enqueue(db, "done1"), _enqueue(db, "fail1")
    db.transition(d, "done", "filed")
    db.transition(f, "failed", "no match")
    db.add_grab_event(d, "info", "extra")

    removed = db.delete_completed_grabs()

    assert removed == 1
    assert db.get_grab_item(d) is None
    assert db.get_grab_events(d) == []
    # Failed item and its history survive.
    assert db.get_grab_item(f) is not None


def test_delete_completed_grabs_none_is_zero(tmp_path):
    db = _db(tmp_path)
    _enqueue(db, "still-pending")
    assert db.delete_completed_grabs() == 0


def test_grabbed_total_counter_survives_clear(tmp_path):
    db = _db(tmp_path)
    assert db.get_grabbed_total() == 0
    db.bump_grabbed_total()
    db.bump_grabbed_total()
    assert db.get_grabbed_total() == 2
    # A completed item then cleared — the lifetime tally must not drop.
    d = _enqueue(db, "done1")
    db.transition(d, "done", "filed")
    db.delete_completed_grabs()
    assert db.get_grabbed_total() == 2


def test_grabbed_total_seeds_from_managed_on_migration(tmp_path):
    path = str(tmp_path / "bpm.db")
    db = BPMDatabase(path)
    for i in range(3):
        db.record_managed_track(
            f"/music/g{i}.mp3", f"h{i}",
            {"title": f"t{i}", "artist": "a", "norm_title": f"t{i}", "norm_artist": "a"},
            120.0, None, None, 120.0, 0.9, "librosa", f"sp{i}")
    # Simulate a database created before the counter existed, then reopen so the
    # migration re-seeds grabbed_total from the current managed-track count.
    with db._connect() as conn:
        conn.execute("DELETE FROM app_counters")
        conn.commit()
    assert BPMDatabase(path).get_grabbed_total() == 3


def test_grabbed_track_still_matches_after_completed_cleared(tmp_path):
    """A grabbed file is recognised as 'have' by its stamped spotify_track_id even
    with no ISRC and a title/artist that would never fuzzy-match — so clearing the
    completed queue row can never cause the sync to re-grab it."""
    db = _db(tmp_path)
    db.record_managed_track(
        "/music/g.mp3", "h1",
        {"title": "weird local filename", "artist": "x", "isrc": "", "duration_ms": 123000,
         "norm_title": m.normalize_title("weird local filename"),
         "norm_artist": m.normalize_artist("x")},
        128.0, None, None, 128.0, 0.9, "librosa", "sp_keep")

    assert db.find_by_spotify_id("sp_keep")[0]["file_path"] == "/music/g.mp3"

    # The Spotify-side metadata (what a sync reconciles) differs entirely, yet the
    # stamped id still resolves it to the grabbed file.
    sp = {"title": "Proper Title", "artist": "Proper Artist", "album": None,
          "duration_ms": 200000, "isrc": "", "spotify_track_id": "sp_keep",
          "norm_title": m.normalize_title("Proper Title"),
          "norm_artist": m.normalize_artist("Proper Artist")}
    assert m.library_match(sp, db) == "/music/g.mp3"


def test_delete_grab_leaves_siblings_intact(tmp_path):
    db = _db(tmp_path)
    a, b = _enqueue(db, "a"), _enqueue(db, "b")
    db.add_grab_event(a, "failed", "a-fail")
    db.add_grab_event(b, "failed", "b-fail")

    db.delete_grab(a)

    assert db.get_grab_item(a) is None
    assert db.get_grab_item(b) is not None
    assert any(e["detail"] == "b-fail" for e in db.get_grab_events(b))
