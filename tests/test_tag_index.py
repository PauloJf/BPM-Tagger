"""read_tags + scanner.index_tags → DB tag index (grabber library matching)."""

import numpy as np
import soundfile as sf
from mutagen.flac import FLAC

from bpm_tagger.bpm.tags import read_tags
from bpm_tagger.db import BPMDatabase
from bpm_tagger.scan.scanner import BPMTagger


def _tagged_flac(path, **tags):
    sr = 22050
    y = (0.1 * np.sin(2 * np.pi * 220 * np.arange(sr * 2) / sr)).astype("float32")
    sf.write(str(path), y, sr, format="FLAC")
    audio = FLAC(str(path))
    for k, v in tags.items():
        audio[k] = v
    audio.save()


def test_read_tags_flac(tmp_path):
    f = tmp_path / "song.flac"
    _tagged_flac(f, title="Blinding Lights", artist="The Weeknd", album="After Hours",
                 albumartist="The Weeknd", tracknumber="3", date="2020", isrc="USUG11904206")
    t = read_tags(str(f))
    assert t["title"] == "Blinding Lights"
    assert t["artist"] == "The Weeknd"
    assert t["album"] == "After Hours"
    assert t["album_artist"] == "The Weeknd"
    assert t["track_no"] == 3
    assert t["year"] == 2020
    assert t["isrc"] == "USUG11904206"
    assert t["duration_ms"] and t["duration_ms"] > 1000


def _tagger(tmp_path):
    config = {
        "db_path": str(tmp_path / "bpm.db"),
        "music_dir": str(tmp_path / "music"),
        "extensions": {".flac", ".mp3"},
        "index_tags": True,
    }
    return BPMTagger(config)


def test_index_tags_populates_norm_columns(tmp_path):
    (tmp_path / "music").mkdir()
    f = tmp_path / "music" / "song.flac"
    _tagged_flac(f, title="Café del Mar", artist="Energy 52", album="Test")

    tagger = _tagger(tmp_path)
    db: BPMDatabase = tagger.db
    # Register the file as a normal analyzed track first.
    from bpm_tagger.bpm.tags import get_file_hash
    db.upsert_track(str(f), get_file_hash(str(f)), 130.0, None, None, 130.0, 0.9,
                    "librosa", "done")

    assert tagger.index_tags() == 1
    row = db.get_track(str(f))
    assert row["title"] == "Café del Mar"
    assert row["norm_title"] == "cafe del mar"        # diacritics stripped
    assert row["norm_artist"] == "energy 52"
    assert row["tags_indexed_hash"] == get_file_hash(str(f))

    # Second pass is a no-op (hash unchanged → nothing needs indexing).
    assert tagger.index_tags() == 0


def test_index_tags_disabled(tmp_path):
    (tmp_path / "music").mkdir()
    f = tmp_path / "music" / "song.flac"
    _tagged_flac(f, title="X", artist="Y")
    tagger = _tagger(tmp_path)
    tagger.config["index_tags"] = False
    from bpm_tagger.bpm.tags import get_file_hash
    tagger.db.upsert_track(str(f), get_file_hash(str(f)), 120.0, None, None, 120.0, 0.9,
                           "librosa", "done")
    assert tagger.index_tags() == 0
