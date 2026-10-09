"""Genre tags in the tag index (core): read, split into track_genres, and the
one-time re-index an upgraded library gets."""

import sqlite3

import numpy as np
import pytest
import soundfile as sf
from mutagen.flac import FLAC

from bpm_tagger.bpm.tags import get_file_hash, read_tags
from bpm_tagger.db import BPMDatabase
from bpm_tagger.text import split_genres

pytestmark = pytest.mark.core  # runs in the core-only CI job


def _flac(path, **tags):
    sf.write(str(path), np.zeros(22050, dtype="float32"), 22050, format="FLAC")
    audio = FLAC(str(path))
    for k, v in tags.items():
        audio[k] = v
    audio.save()


@pytest.mark.parametrize("raw,expected", [
    ("House", ["House"]),
    ("Electronic; House", ["Electronic", "House"]),
    ("Rock/Pop, Indie", ["Rock", "Pop", "Indie"]),
    ("Drum & Bass", ["Drum & Bass"]),
    ("House; house", ["House"]),
    ("", []),
    (None, []),
])
def test_split_genres(raw, expected):
    assert split_genres(raw) == expected


def test_read_tags_joins_every_genre_value(tmp_path):
    f = tmp_path / "a.flac"
    _flac(f, genre=["Techno", "Minimal"])
    assert read_tags(str(f))["genre"] == "Techno; Minimal"


def test_read_tags_without_genre(tmp_path):
    f = tmp_path / "b.flac"
    _flac(f, title="x")
    assert read_tags(str(f))["genre"] is None


def test_update_track_tags_fills_track_genres(tmp_path):
    db = BPMDatabase(str(tmp_path / "bpm.db"))
    f = tmp_path / "c.flac"
    _flac(f, genre=["Deep House; Techno"])
    db.bulk_register_pending([(str(f), get_file_hash(str(f)))])
    db.update_track_tags(str(f), read_tags(str(f)), get_file_hash(str(f)))
    with sqlite3.connect(db.db_path) as conn:
        rows = conn.execute("SELECT name, norm_name FROM track_genres ORDER BY name").fetchall()
        genre = conn.execute("SELECT genre FROM tracks").fetchone()[0]
    assert genre == "Deep House; Techno"
    assert rows == [("Deep House", "deep house"), ("Techno", "techno")]

    # Re-indexing replaces, never accumulates.
    _flac(f, genre=["Ambient"])
    db.update_track_tags(str(f), read_tags(str(f)), get_file_hash(str(f)))
    with sqlite3.connect(db.db_path) as conn:
        assert conn.execute("SELECT name FROM track_genres").fetchall() == [("Ambient",)]


def test_upgrade_forces_one_tag_reindex(tmp_path):
    """A DB from before genres: adding the column clears every tag-index hash,
    so the next index_tags() pass re-reads tags (and so picks up genres)."""
    path = tmp_path / "old.db"
    BPMDatabase(str(path))  # current schema
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO tracks (file_path, file_hash, tags_indexed_hash, status) "
                     "VALUES ('/m/a.flac', 'h1', 'h1', 'done')")
        conn.execute("ALTER TABLE tracks DROP COLUMN genre")  # simulate the old schema
        conn.commit()
    db = BPMDatabase(str(path))
    assert [r["file_path"] for r in db.get_tracks_needing_tag_index()] == ["/m/a.flac"]
    # …once: a DB that already has the column keeps its hashes.
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE tracks SET tags_indexed_hash = file_hash")
        conn.commit()
    assert BPMDatabase(str(path)).get_tracks_needing_tag_index() == []
