"""ffmpeg transcode to the one configured output format (§5).

Every download is normalized to a single profile. If the source already matches
the target container it's copied as-is (no re-encode). Lossy→lossy conversions
are flagged so the queue can surface a quality warning.
"""

import logging
import os
import shutil
import subprocess

log = logging.getLogger(__name__)

# profile → (extension, ffmpeg audio args)
PROFILES = {
    "mp3-320":  ("mp3",  ["-c:a", "libmp3lame", "-b:a", "320k"]),
    "mp3-128":  ("mp3",  ["-c:a", "libmp3lame", "-b:a", "128k"]),
    "flac":     ("flac", ["-c:a", "flac"]),
    "opus-192": ("opus", ["-c:a", "libopus", "-b:a", "192k"]),
}

_LOSSY = {"mp3", "opus", "aac", "ogg", "m4a", "wma"}


def profile_ext(profile: str) -> str:
    return PROFILES.get(profile, PROFILES["mp3-320"])[0]


def build_ffmpeg_args(src: str, dest: str, profile: str) -> list[str]:
    _, codec_args = PROFILES.get(profile, PROFILES["mp3-320"])
    # -vn drops any embedded cover video stream; cover art is (re)embedded later.
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", src, "-vn", *codec_args, dest]


def transcode(src: str, dest_dir: str, profile: str, base_name: str) -> tuple[str, str | None]:
    """Transcode `src` into `dest_dir/base_name.<ext>`.

    Returns (dest_path, warning_or_None). Copies instead of re-encoding when the
    source already matches the target container.
    """
    os.makedirs(dest_dir, exist_ok=True)
    target_ext = profile_ext(profile)
    dest = os.path.join(dest_dir, f"{base_name}.{target_ext}")
    src_ext = os.path.splitext(src)[1].lstrip(".").lower()

    warning = None
    if src_ext == target_ext:
        shutil.copy2(src, dest)
        return dest, None

    if src_ext in _LOSSY and target_ext in _LOSSY:
        warning = f"lossy→lossy transcode ({src_ext}→{target_ext}) — quality loss"
        log.warning("%s: %s", os.path.basename(src), warning)

    args = build_ffmpeg_args(src, dest, profile)
    try:
        subprocess.run(args, check=True, capture_output=True)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg not found on PATH") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", "ignore")[:400] if exc.stderr else ""
        raise RuntimeError(f"ffmpeg failed: {stderr}") from exc
    return dest, warning
