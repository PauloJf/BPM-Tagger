"""delete_grab(): removing a queue item also removes its candidates and events.

The Delete action on the queue page (failed/skipped rows) drops the bookkeeping
row and its children in one transaction — explicitly, so it's correct on older
databases that predate ON DELETE CASCADE.
"""

from bpm_tagger.db import BPMDatabase


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


def test_delete_grab_leaves_siblings_intact(tmp_path):
    db = _db(tmp_path)
    a, b = _enqueue(db, "a"), _enqueue(db, "b")
    db.add_grab_event(a, "failed", "a-fail")
    db.add_grab_event(b, "failed", "b-fail")

    db.delete_grab(a)

    assert db.get_grab_item(a) is None
    assert db.get_grab_item(b) is not None
    assert any(e["detail"] == "b-fail" for e in db.get_grab_events(b))
