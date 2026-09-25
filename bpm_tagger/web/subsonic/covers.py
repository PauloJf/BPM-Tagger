"""Cover art for Subsonic ``getCoverArt`` — cheap enough that a grid of covers
can't starve audio streams.

Apps load album/artist grids with dozens of parallel ``getCoverArt?size=``
calls. Decoding and resizing a large embedded JPEG costs tens of milliseconds
per call on a desktop and several times that on a NAS, and Python runs that
work one core at a time — so an uncached grid used to occupy every server
thread for seconds, and the app's next audio request queued behind it
(playback stalled). Three things keep it cheap:

* **Disk cache** of resized covers under ``<data>/subsonic_covers``, keyed by
  the source file, its hash (size:mtime — so a cover edit invalidates it) and
  the requested size. Each cover is resized once, ever.
* **Fast resize**: JPEGs are decoded at reduced scale (PIL ``draft``) instead of
  decoding the full image and then shrinking it.
* **A cap on concurrent first-time resizes** (``MAX_RENDERS``). Over the cap the
  original image is sent immediately — more bytes, no CPU — so a cold grid
  never ties up the threads streams need. It's resized on a later request.

"No art" results are remembered for a while too, so an unillustrated album
isn't re-parsed on every scroll.
"""

import hashlib
import io
import logging
import os
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

MAX_RENDERS = 2
_render_slots = threading.BoundedSemaphore(MAX_RENDERS)
MIN_SIZE, MAX_SIZE = 32, 2048
MAX_CACHE_FILES = 20_000
NO_ART_TTL = 600

_FOLDER_COVERS = ("cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "folder.jpeg",
                  "folder.png", "front.jpg", "front.png")

_no_art: dict[str, float] = {}      # "path|hash" → expiry
_no_art_lock = threading.Lock()
_writes = [0]


def cache_dir(config: dict) -> str:
    return os.path.join(os.path.dirname(config.get("db_path") or "") or ".", "subsonic_covers")


def _folder_cover(directory: str):
    for name in _FOLDER_COVERS:
        p = os.path.join(directory, name)
        if os.path.isfile(p):
            with open(p, "rb") as f:
                return f.read(), "image/png" if name.endswith(".png") else "image/jpeg"
    return None


def _source(path: str):
    """(bytes, mime) of a track's art — embedded first, then a folder image."""
    from ...grabber.tagging import read_cover
    cover = read_cover(path) or _folder_cover(os.path.dirname(path))
    if cover and not (cover[1] or "").lower().startswith("image/"):
        cover = (cover[0], "application/octet-stream")
    return cover


def _resize(data: bytes, size: int) -> Optional[bytes]:
    """JPEG bytes at most ``size`` px on the long edge, or None if the image is
    already that small (or unreadable) — then the original is served."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        if max(img.size) <= size:
            return None
        if img.format == "JPEG":
            img.draft("RGB", (size, size))   # decode at 1/2, 1/4 or 1/8 scale
        img = img.convert("RGB")
        img.thumbnail((size, size))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=85)
        return out.getvalue()
    except Exception:  # Pillow missing or an image it can't read
        return None


def _prune(directory: str) -> None:
    try:
        entries = [e for e in os.scandir(directory) if e.is_file()]
    except OSError:
        return
    if len(entries) <= MAX_CACHE_FILES:
        return
    entries.sort(key=lambda e: e.stat().st_atime)
    for e in entries[: len(entries) - int(MAX_CACHE_FILES * 0.9)]:
        try:
            os.remove(e.path)
        except OSError:
            pass


def image_file(config: dict, path: str, size: Optional[int]):
    """(bytes, mime) of an image file (an artist photo), resized and cached like
    track art. Keyed on the file's mtime, so replacing the photo refreshes it."""
    try:
        with open(path, "rb") as f:
            data = f.read()
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    if size is None:
        return data, mime
    size = max(MIN_SIZE, min(MAX_SIZE, size))
    directory = cache_dir(config)
    cached = os.path.join(directory, hashlib.sha1(
        f"file|{path}|{mtime}|{size}".encode("utf-8")).hexdigest() + ".jpg")
    try:
        with open(cached, "rb") as f:
            return f.read(), "image/jpeg"
    except OSError:
        pass
    if not _render_slots.acquire(blocking=False):
        return data, mime
    try:
        small = _resize(data, size)
    finally:
        _render_slots.release()
    if small is None:
        return data, mime
    try:
        os.makedirs(directory, exist_ok=True)
        tmp = f"{cached}.{threading.get_ident()}.tmp"
        with open(tmp, "wb") as f:
            f.write(small)
        os.replace(tmp, cached)
    except OSError as exc:
        log.debug("Cover cache write failed: %s", exc)
    return small, "image/jpeg"


def cover_for(config: dict, tracks: list, size: Optional[int]):
    """(bytes, mime) for the first of ``tracks`` that has art, or None."""
    if size is not None:
        size = max(MIN_SIZE, min(MAX_SIZE, size))
    directory = cache_dir(config)
    now = time.monotonic()
    for track in tracks:
        path = track["file_path"]
        ident = f"{path}|{track.get('file_hash') or ''}"
        with _no_art_lock:
            if _no_art.get(ident, 0) > now:
                continue
        cached = None
        if size is not None:
            cached = os.path.join(directory, hashlib.sha1(
                f"{ident}|{size}".encode("utf-8")).hexdigest() + ".jpg")
            try:
                with open(cached, "rb") as f:
                    return f.read(), "image/jpeg"
            except OSError:
                pass
        cover = _source(path)
        if not cover:
            with _no_art_lock:
                _no_art[ident] = now + NO_ART_TTL
                if len(_no_art) > 50_000:
                    _no_art.clear()
            continue
        data, mime = cover
        if size is None or not _render_slots.acquire(blocking=False):
            return data, mime       # no size asked, or every render slot busy
        try:
            small = _resize(data, size)
        finally:
            _render_slots.release()
        if small is None:
            return data, mime
        try:
            os.makedirs(directory, exist_ok=True)
            tmp = f"{cached}.{threading.get_ident()}.tmp"
            with open(tmp, "wb") as f:
                f.write(small)
            os.replace(tmp, cached)
            _writes[0] += 1
            if _writes[0] % 500 == 0:
                _prune(directory)
        except OSError as exc:
            log.debug("Cover cache write failed: %s", exc)
        return small, "image/jpeg"
    return None
