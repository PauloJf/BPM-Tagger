"""grab_events retention (DB-2): the per-item audit log is bounded.

grab_queue rows persist indefinitely, so without a cap a long-lived / repeatedly
retried item's event history would grow without bound. Events are capped at write
time, and prune_grab_events() cleans up databases that predate the cap.
"""

from bpm_tagger.db import BPMDatabase


def _db(tmp_path):
    return BPMDatabase(str(tmp_path / "bpm.db"))


def _enqueue(db, title="song"):
    return db.enqueue_grab({"title": title, "artist": "A", "spotify_track_id": f"sp_{title}"})


def test_events_capped_per_item_at_write_time(tmp_path):
    db = _db(tmp_path)
    item_id = _enqueue(db)
    cap = BPMDatabase._EVENTS_PER_ITEM_CAP
    # Far more transitions than the cap (statuses reused; audit-only).
    for i in range(cap + 60):
        db.add_grab_event(item_id, "warning", f"note {i}")
    events = db.get_grab_events(item_id)
    assert len(events) <= cap
    # The newest event is retained (most recent note), the oldest dropped.
    details = [e["detail"] for e in events]
    assert f"note {cap + 59}" in details
    assert "note 0" not in details


def test_prune_cleans_pre_cap_bloat(tmp_path):
    db = _db(tmp_path)
    item_id = _enqueue(db)
    cap = BPMDatabase._EVENTS_PER_ITEM_CAP
    # Simulate legacy bloat by inserting straight past the cap-enforcing helper.
    with db._connect() as conn:
        conn.executemany(
            "INSERT INTO grab_events (queue_item_id, event, detail, created_at) "
            "VALUES (?, 'x', ?, '2020-01-01T00:00:00')",
            [(item_id, str(i)) for i in range(cap + 200)],
        )
        conn.commit()
        raw = conn.execute("SELECT COUNT(*) FROM grab_events WHERE queue_item_id=?",
                           (item_id,)).fetchone()[0]
    assert raw > cap                       # bloat present
    removed = db.prune_grab_events()
    assert removed >= 200
    assert len(db.get_grab_events(item_id)) <= cap


def test_prune_keeps_recent_events_across_items(tmp_path):
    db = _db(tmp_path)
    a, b = _enqueue(db, "a"), _enqueue(db, "b")
    db.add_grab_event(a, "warning", "a-note")
    db.add_grab_event(b, "warning", "b-note")
    db.prune_grab_events()
    # Each item keeps its (well-under-cap) events — prune only trims the excess.
    assert any(e["detail"] == "a-note" for e in db.get_grab_events(a))
    assert any(e["detail"] == "b-note" for e in db.get_grab_events(b))
