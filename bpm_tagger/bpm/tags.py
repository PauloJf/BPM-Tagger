"""Tag writing and fast file-change hashing."""

import logging
import os
from pathlib import Path

import mutagen
from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError, TBPM
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis

log = logging.getLogger(__name__)


def write_bpm_tag(file_path: str, bpm: float) -> bool:
    ext = Path(file_path).suffix.lower()
    bpm_str = str(round(bpm))
    try:
        if ext == ".mp3":
            try:
                tags = ID3(file_path)
            except ID3NoHeaderError:
                tags = ID3()
            tags["TBPM"] = TBPM(encoding=3, text=bpm_str)
            tags.save(file_path)
        elif ext == ".flac":
            audio = FLAC(file_path)
            audio["BPM"] = bpm_str
            audio.save()
        elif ext in (".m4a", ".aac"):
            audio = MP4(file_path)
            audio["tmpo"] = [round(bpm)]
            audio.save()
        elif ext in (".ogg", ".opus"):
            audio = OggVorbis(file_path)
            audio["BPM"] = bpm_str
            audio.save()
        else:
            audio = mutagen.File(file_path)
            if audio is None:
                return False
            audio["BPM"] = bpm_str
            audio.save()
        return True
    except Exception as exc:
        log.error("Failed to write tag for %s: %s", file_path, exc)
        return False


def get_file_hash(file_path: str) -> str:
    stat = os.stat(file_path)
    return f"{stat.st_size}:{stat.st_mtime}"
