"""On-the-fly transcoding for Subsonic ``stream`` (Phase 3, opt-in).

Off by default (``SUBSONIC_TRANSCODE``): it costs CPU on every play, and files
stream fine as-is on a LAN. When on, a client's ``format`` / ``maxBitRate``
decide whether a file is re-encoded:

* ``format=raw`` → never;
* ``format=mp3|opus`` → re-encode unless the file already is that format and
  within the bitrate cap;
* no format, but ``maxBitRate`` below the file's bitrate → re-encode to the
  default format (opus when the ffmpeg build has libopus, else mp3).

ffmpeg writes to a pipe, and its stdout is streamed to the client; the process
is killed when the response closes (skip, seek, disconnect). Concurrent
transcodes are capped: over the cap, the original file is served instead, so a
burst of clients can't pin every core. A transcoded stream can't serve HTTP
ranges, so seeking uses ``timeOffset`` (seconds), which clients send when
restarting a stream mid-track.
"""

import logging
import os
import shutil
import subprocess
import threading
from functools import lru_cache
from typing import Optional

from flask import Response

log = logging.getLogger(__name__)

MAX_CONCURRENT = 4
_slots = threading.BoundedSemaphore(MAX_CONCURRENT)

# format → (ffmpeg codec args without bitrate, container, content type, suffix)
FORMATS = {
    "mp3":  (["-c:a", "libmp3lame"], "mp3", "audio/mpeg", "mp3"),
    "opus": (["-c:a", "libopus", "-vbr", "on"], "ogg", "audio/ogg", "opus"),
}
_BITRATE_MIN, _BITRATE_MAX, _BITRATE_DEFAULT = 32, 320, 192
_CHUNK = 64 * 1024


def ffmpeg_path() -> Optional[str]:
    return shutil.which("ffmpeg")


@lru_cache(maxsize=1)
def _has_libopus() -> bool:
    exe = ffmpeg_path()
    if not exe:
        return False
    try:
        out = subprocess.run([exe, "-hide_banner", "-encoders"], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "libopus" in out


def default_format() -> str:
    return "opus" if _has_libopus() else "mp3"


def plan(path: str, source_kbps: Optional[int], fmt: str, max_kbps: int):
    """(target_format, kbps) to transcode to, or None to serve the file as-is."""
    fmt = (fmt or "").lower()
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if fmt == "raw" or not ffmpeg_path():
        return None
    cap = max_kbps if max_kbps and max_kbps > 0 else None
    if fmt in FORMATS:
        if fmt == "opus" and not _has_libopus():
            fmt = "mp3"
        if ext == fmt and (cap is None or (source_kbps and source_kbps <= cap)):
            return None
        target = fmt
    elif cap is not None and (source_kbps is None or source_kbps > cap):
        target = default_format()
    else:
        return None  # unknown/absent format and no binding bitrate cap
    kbps = cap or _BITRATE_DEFAULT
    return target, max(_BITRATE_MIN, min(_BITRATE_MAX, kbps))


def stream_response(path: str, target: str, kbps: int, offset: float = 0.0,
                    duration_s: Optional[int] = None,
                    estimate_length: bool = False) -> Optional[Response]:
    """A streaming Response of ``path`` re-encoded, or None when every transcode
    slot is busy (the caller then serves the original file)."""
    if not _slots.acquire(blocking=False):
        log.info("Subsonic transcode: all %d slots busy, serving original", MAX_CONCURRENT)
        return None
    codec, container, mimetype, _suffix = FORMATS[target]
    cmd = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-nostdin"]
    if offset > 0:
        cmd += ["-ss", f"{offset:.3f}"]
    cmd += ["-i", path, "-map", "0:a:0", "-vn", *codec, "-b:a", f"{kbps}k",
            "-f", container, "pipe:1"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL)
    except OSError as exc:
        _slots.release()
        log.warning("Subsonic transcode failed to start: %s", exc)
        return None

    done_lock = threading.Lock()
    done = [False]

    def _cleanup():
        # Runs from the generator's finally AND the response close hook; the
        # slot must be released exactly once.
        with done_lock:
            if done[0]:
                return
            done[0] = True
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover
            pass
        if proc.stdout:
            proc.stdout.close()
        _slots.release()

    def _generate():
        try:
            while True:
                chunk = proc.stdout.read(_CHUNK)
                if not chunk:
                    break
                yield chunk
        finally:
            _cleanup()

    resp = Response(_generate(), mimetype=mimetype, direct_passthrough=True)
    resp.headers["Accept-Ranges"] = "none"
    if estimate_length and duration_s:
        remaining = max(0.0, duration_s - offset)
        resp.headers["Content-Length"] = str(int(remaining * kbps * 1000 / 8))
    resp.call_on_close(_cleanup)
    return resp
