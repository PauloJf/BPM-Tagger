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


def write_bpm_tag(file_path: str, bpm: float, preserve_mtime: bool = True) -> bool:
    ext = Path(file_path).suffix.lower()
    bpm_str = str(round(bpm))
    try:
        # Capture the timestamps before the tag write so we can restore them.
        # Media servers (Navidrome) and backup tools key off mtime; without this
        # every tagged file looks freshly modified.
        st = os.stat(file_path) if preserve_mtime else None
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
            if audio.tags is None:
                audio.add_tags()
            if isinstance(audio.tags, ID3):
                # ID3-backed containers (WAV, AIFF, DSF) require Frame
                # instances, not raw strings.
                audio.tags.add(TBPM(encoding=3, text=bpm_str))
            else:
                audio["BPM"] = bpm_str
            audio.save()
        if st is not None:
            os.utime(file_path, (st.st_atime, st.st_mtime))
        return True
    except Exception as exc:
        log.error("Failed to write tag for %s: %s", file_path, exc)
        return False


def get_file_hash(file_path: str) -> str:
    stat = os.stat(file_path)
    return f"{stat.st_size}:{stat.st_mtime}"


def _first(v):
    if isinstance(v, (list, tuple)):
        return v[0] if v else None
    return v


def _int_prefix(v):
    """Parse '5', '5/12', 5 → 5; None on failure."""
    s = _first(v)
    if s is None:
        return None
    try:
        return int(str(s).split("/")[0].strip())
    except (ValueError, TypeError):
        return None


def _read_isrc(file_path: str, easy) -> str | None:
    try:
        v = _first(easy.get("isrc"))
        if v:
            return str(v)
    except Exception:
        pass
    # Format-specific fallbacks (EasyID3 doesn't always expose ISRC).
    ext = Path(file_path).suffix.lower()
    try:
        if ext == ".mp3":
            id3 = ID3(file_path)
            fr = id3.getall("TSRC")
            if fr:
                return str(fr[0].text[0])
        elif ext in (".m4a", ".aac"):
            mp4 = MP4(file_path)
            for key in ("----:com.apple.iTunes:ISRC", "----:com.apple.iTunes:isrc"):
                if key in mp4:
                    raw = mp4[key][0]
                    return raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else str(raw)
    except Exception:
        pass
    return None


def read_tags(file_path: str) -> dict:
    """Best-effort metadata read for the grabber tag index. Missing fields → None."""
    out = {"title": None, "artist": None, "album": None, "album_artist": None,
           "track_no": None, "disc_no": None, "year": None, "isrc": None,
           "duration_ms": None}
    try:
        easy = mutagen.File(file_path, easy=True)
        if easy is None:
            return out
        out["title"] = _first(easy.get("title"))
        out["artist"] = _first(easy.get("artist"))
        out["album"] = _first(easy.get("album"))
        out["album_artist"] = _first(easy.get("albumartist")) or _first(easy.get("album artist"))
        out["track_no"] = _int_prefix(easy.get("tracknumber"))
        out["disc_no"] = _int_prefix(easy.get("discnumber"))
        date = _first(easy.get("date")) or _first(easy.get("year"))
        if date:
            digits = "".join(c for c in str(date)[:4] if c.isdigit())
            out["year"] = int(digits) if len(digits) == 4 else None
        out["isrc"] = _read_isrc(file_path, easy)
        if getattr(easy, "info", None) and getattr(easy.info, "length", None):
            out["duration_ms"] = int(easy.info.length * 1000)
    except Exception as exc:
        log.debug("read_tags failed for %s: %s", file_path, exc)
    return out
