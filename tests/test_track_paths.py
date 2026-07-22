"""``get_track_paths`` feeds the player's Play All / Shuffle queue. It must
return each track's tag ``title`` (not just the file path) so the player bar and
queue show the real title instead of a filename."""

import pytest

from bpm_tagger.db import BPMDatabase


@pytest.fixture
def db(tmp_path):
    return BPMDatabase(str(tmp_path / "bpm.db"))


def _seed(db, path, title, artist="Artist"):
    with db._connect() as conn:
        conn.execute(
            "INSERT INTO tracks (file_path, title, artist, status) "
            "VALUES (?, ?, ?, 'done')",
            (path, title, artist))
        conn.commit()


def test_get_track_paths_includes_title(db):
    _seed(db, "/music/a.mp3", "Real Title", "Some Artist")
    rows = db.get_track_paths()
    assert len(rows) == 1
    row = rows[0]
    assert row["file_path"] == "/music/a.mp3"
    assert row["title"] == "Real Title"
    assert row["artist"] == "Some Artist"


def test_get_track_paths_title_may_be_null(db):
    # An untagged file has no title; the row still carries the key (None), and
    # the frontend falls back to the basename.
    with db._connect() as conn:
        conn.execute(
            "INSERT INTO tracks (file_path, status) VALUES ('/music/b.mp3', 'done')")
        conn.commit()
    rows = db.get_track_paths()
    assert rows[0]["title"] is None
