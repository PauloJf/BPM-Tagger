"""Read/write lyrics on audio files (embedded tags or a .lrc sidecar).

Synced lyrics are stored as LRC-formatted text inside the standard *unsynced*
fields (USLT / LYRICS= / ©lyr) — Navidrome and most players parse the LRC
timestamps out of those, so no SYLT frames are needed. Embedded writes follow
the same mtime-preservation contract as bpm.tags.write_bpm_tag; callers must
still refresh the DB file hash afterwards (the file size changes).
"""

import logging
import os
import re
from pathlib import Path

import mutagen
from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError, USLT
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis

log = logging.getLogger(__name__)

# An LRC timestamp at a line start, e.g. [01:23.45] — presence means "synced".
_LRC_TS_RE = re.compile(r"^\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]", re.MULTILINE)

_VORBIS_KEYS = ("LYRICS", "UNSYNCEDLYRICS")


def is_synced(text: str | None) -> bool:
    return bool(text and _LRC_TS_RE.search(text))


def sidecar_path(file_path: str) -> str:
    return str(Path(file_path).with_suffix(".lrc"))


def _read_embedded(file_path: str) -> str | None:
    ext = Path(file_path).suffix.lower()
    try:
        if ext == ".mp3":
            frames = ID3(file_path).getall("USLT")
            if frames and str(frames[0].text).strip():
                return str(frames[0].text)
        elif ext in (".flac", ".ogg", ".opus"):
            audio = FLAC(file_path) if ext == ".flac" else mutagen.File(file_path)
            if audio:
                for key in _VORBIS_KEYS:
                    vals = audio.get(key)
                    if vals and str(vals[0]).strip():
                        return str(vals[0])
        elif ext in (".m4a", ".aac", ".mp4"):
            vals = MP4(file_path).get("\xa9lyr")
            if vals and str(vals[0]).strip():
                return str(vals[0])
    except Exception as exc:
        log.debug("Lyrics read failed for %s: %s", os.path.basename(file_path), exc)
    return None


def read_lyrics(file_path: str) -> tuple[str, str] | None:
    """Return (lyrics_text, source) — source is 'embedded' or 'sidecar' — or None."""
    text = _read_embedded(file_path)
    if text:
        return text, "embedded"
    sc = sidecar_path(file_path)
    if os.path.isfile(sc):
        try:
            text = Path(sc).read_text(encoding="utf-8", errors="replace").strip()
            if text:
                return text, "sidecar"
        except Exception as exc:
            log.debug("Sidecar read failed for %s: %s", sc, exc)
    return None


def _write_embedded(file_path: str, text: str) -> bool:
    ext = Path(file_path).suffix.lower()
    if ext == ".mp3":
        try:
            tags = ID3(file_path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall("USLT")
        if text:
            tags["USLT"] = USLT(encoding=3, lang="XXX", desc="", text=text)
        tags.save(file_path)
    elif ext == ".flac":
        audio = FLAC(file_path)
        for key in _VORBIS_KEYS:
            audio.pop(key, None)
        if text:
            audio["LYRICS"] = text
        audio.save()
    elif ext in (".ogg", ".opus"):
        audio = OggVorbis(file_path) if ext == ".ogg" else mutagen.File(file_path)
        if audio is None:
            return False
        for key in _VORBIS_KEYS:
            audio.pop(key, None)
        if text:
            audio["LYRICS"] = text
        audio.save()
    elif ext in (".m4a", ".aac", ".mp4"):
        audio = MP4(file_path)
        if text:
            audio["\xa9lyr"] = [text]
        else:
            audio.pop("\xa9lyr", None)
        audio.save()
    else:
        return False
    return True


def write_lyrics(file_path: str, text: str, mode: str = "embed",
                 preserve_mtime: bool = True) -> bool:
    """Write (or, with empty text, remove) a track's lyrics.

    mode 'embed' writes the tag; 'sidecar' writes a .lrc next to the file.
    Removal clears both places so "delete lyrics" means what it says.
    """
    text = (text or "").strip()
    try:
        st = os.stat(file_path) if preserve_mtime else None
        if not text:
            ok = _write_embedded(file_path, "")
            sc = sidecar_path(file_path)
            if os.path.isfile(sc):
                os.remove(sc)
        elif mode == "sidecar":
            Path(sidecar_path(file_path)).write_text(text + "\n", encoding="utf-8")
            ok = True
        else:
            ok = _write_embedded(file_path, text)
        if st is not None:
            os.utime(file_path, (st.st_atime, st.st_mtime))
        return ok
    except Exception as exc:
        log.error("Failed to write lyrics for %s: %s", file_path, exc)
        return False
