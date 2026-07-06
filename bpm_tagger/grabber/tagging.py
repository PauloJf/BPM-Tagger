"""Write full metadata + embed cover art onto a downloaded, transcoded file (§5).

BPM itself is written separately by bpm.tags.write_bpm_tag in the analyzing_bpm
step, so this module only handles the descriptive tags + artwork.
"""

import base64
import logging
import os

import mutagen
import requests
from mutagen.flac import FLAC, Picture
from mutagen.id3 import (APIC, ID3, ID3NoHeaderError, TALB, TDRC, TIT2, TPE1,
                         TPE2, TPOS, TRCK, TSRC)
from mutagen.mp4 import MP4, MP4Cover

log = logging.getLogger(__name__)


def _track_disc(n) -> str:
    return str(n) if n else ""


def write_track_tags(file_path: str, meta: dict) -> None:
    """Write descriptive tags (title/artist/album/album_artist/track/disc/year/isrc)."""
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".mp3":
            try:
                tags = ID3(file_path)
            except ID3NoHeaderError:
                tags = ID3()
            if meta.get("title"):        tags["TIT2"] = TIT2(encoding=3, text=meta["title"])
            if meta.get("artist"):       tags["TPE1"] = TPE1(encoding=3, text=meta["artist"])
            if meta.get("album"):        tags["TALB"] = TALB(encoding=3, text=meta["album"])
            if meta.get("album_artist"): tags["TPE2"] = TPE2(encoding=3, text=meta["album_artist"])
            if meta.get("track_no"):     tags["TRCK"] = TRCK(encoding=3, text=_track_disc(meta["track_no"]))
            if meta.get("disc_no"):      tags["TPOS"] = TPOS(encoding=3, text=_track_disc(meta["disc_no"]))
            if meta.get("year"):         tags["TDRC"] = TDRC(encoding=3, text=str(meta["year"]))
            if meta.get("isrc"):         tags["TSRC"] = TSRC(encoding=3, text=meta["isrc"])
            tags.save(file_path)
        else:
            audio = mutagen.File(file_path, easy=True)
            if audio is None:
                return
            def setk(k, v):
                if v:
                    audio[k] = str(v)
            setk("title", meta.get("title"))
            setk("artist", meta.get("artist"))
            setk("album", meta.get("album"))
            setk("albumartist", meta.get("album_artist"))
            setk("tracknumber", _track_disc(meta.get("track_no")))
            setk("discnumber", _track_disc(meta.get("disc_no")))
            setk("date", meta.get("year"))
            setk("isrc", meta.get("isrc"))
            audio.save()
    except Exception as exc:
        log.warning("Tag write failed for %s: %s", os.path.basename(file_path), exc)


def fetch_cover(url: str) -> bytes | None:
    if not url:
        return None
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        return resp.content
    except Exception as exc:
        log.debug("Cover fetch failed (%s): %s", url, exc)
        return None


def embed_cover(file_path: str, image_bytes: bytes, mime: str = "image/jpeg") -> None:
    if not image_bytes:
        return
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".mp3":
            try:
                tags = ID3(file_path)
            except ID3NoHeaderError:
                tags = ID3()
            tags.delall("APIC")
            tags["APIC"] = APIC(encoding=3, mime=mime, type=3, desc="Cover", data=image_bytes)
            tags.save(file_path)
        elif ext == ".flac":
            audio = FLAC(file_path)
            pic = Picture()
            pic.type = 3
            pic.mime = mime
            pic.data = image_bytes
            audio.clear_pictures()
            audio.add_picture(pic)
            audio.save()
        elif ext in (".m4a", ".aac", ".mp4"):
            audio = MP4(file_path)
            fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
            audio["covr"] = [MP4Cover(image_bytes, imageformat=fmt)]
            audio.save()
        elif ext in (".ogg", ".opus"):
            audio = mutagen.File(file_path)
            pic = Picture()
            pic.type = 3
            pic.mime = mime
            pic.data = image_bytes
            audio["metadata_block_picture"] = [base64.b64encode(pic.write()).decode("ascii")]
            audio.save()
    except Exception as exc:
        log.warning("Cover embed failed for %s: %s", os.path.basename(file_path), exc)
