"""Audio loading, with an ffmpeg fallback for what libsndfile can't decode.

librosa used to fall back to audioread (and so to ffmpeg) whenever libsndfile
refused a file. That fallback was deprecated in librosa 0.10 and **removed in
1.0**, so with `librosa>=0.10.0` unpinned the project silently walked into a
world where every AAC file failed with "Format not recognised" — libsndfile has
no AAC support, and nothing picked up the slack. It broke .m4a and .aac, both
listed as supported formats.

So the fallback is ours now: try librosa (fast, in-process, handles wav/flac/
ogg/opus/mp3), and shell out to ffmpeg for anything it refuses. ffmpeg is
already a runtime dependency of the image, and decoding to raw f32 PCM on
stdout keeps this a single pipe with no temporary files.

Every caller that used `librosa.load` goes through `load_audio`, which keeps
librosa's return contract: (samples,) when mono, (channels, samples) otherwise.
"""

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import librosa
import numpy as np

log = logging.getLogger(__name__)

# A decode should never outlive the scan that asked for it.
_FFMPEG_TIMEOUT = 300


class AudioLoadError(RuntimeError):
    """Neither libsndfile nor ffmpeg could decode the file."""


def _probe(file_path: str) -> tuple[Optional[int], Optional[int]]:
    """(sample_rate, channels) from ffprobe, or (None, None) if it can't say."""
    if not shutil.which("ffprobe"):
        return None, None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=sample_rate,channels",
             "-of", "json", file_path],
            capture_output=True, timeout=30, check=True,
        ).stdout
        stream = (json.loads(out).get("streams") or [{}])[0]
        rate = int(stream["sample_rate"]) if stream.get("sample_rate") else None
        chans = int(stream["channels"]) if stream.get("channels") else None
        return rate, chans
    except (subprocess.SubprocessError, ValueError, KeyError, json.JSONDecodeError) as exc:
        log.debug("ffprobe failed for %s: %s", Path(file_path).name, exc)
        return None, None


def _ffmpeg_load(file_path: str, sr: Optional[int], mono: bool,
                 offset: float, duration: Optional[float]) -> tuple[np.ndarray, int]:
    """Decode via ffmpeg to raw float32 PCM. Mirrors librosa.load's shapes."""
    if not shutil.which("ffmpeg"):
        raise AudioLoadError("ffmpeg is not installed, so this format cannot be decoded")

    native_sr, native_ch = _probe(file_path)
    out_sr = sr or native_sr
    if not out_sr:
        raise AudioLoadError("could not determine the sample rate")
    out_ch = 1 if mono else (native_ch or 1)

    # -ss before -i seeks on the input (fast); -t after it limits the output.
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if offset:
        cmd += ["-ss", f"{offset:.6f}"]
    cmd += ["-i", file_path]
    if duration:
        cmd += ["-t", f"{duration:.6f}"]
    cmd += ["-map", "a:0", "-f", "f32le", "-acodec", "pcm_f32le",
            "-ar", str(out_sr), "-ac", str(out_ch), "-"]

    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=_FFMPEG_TIMEOUT, check=True)
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise AudioLoadError(f"ffmpeg could not decode it: {err[-1] if err else 'no output'}") from exc
    except subprocess.SubprocessError as exc:
        raise AudioLoadError(f"ffmpeg failed: {exc}") from exc

    y = np.frombuffer(proc.stdout, dtype=np.float32)
    if out_ch > 1:
        # ffmpeg interleaves; librosa hands back (channels, samples).
        y = y.reshape(-1, out_ch).T
    return np.ascontiguousarray(y), out_sr


def load_audio(file_path: str, *, sr: Optional[int] = None, mono: bool = True,
               offset: float = 0.0, duration: Optional[float] = None
               ) -> tuple[np.ndarray, int]:
    """librosa.load, falling back to ffmpeg for formats libsndfile refuses.

    Returns (samples,) when mono else (channels, samples), and the sample rate —
    librosa's contract, so call sites need no other change.
    """
    try:
        y, out_sr = librosa.load(file_path, sr=sr, mono=mono,
                                 offset=offset, duration=duration)
        # An empty *window* is a legitimate answer — a segment offset can land
        # past the end of the decodable audio, e.g. when a container's declared
        # duration overstates what actually decodes. Only a whole-file read that
        # comes back empty is suspicious enough to be worth an ffmpeg attempt;
        # retrying the rest just spawns a subprocess to be told the same thing.
        if y.size or offset or duration:
            return y, int(out_sr)
        first_error: Optional[Exception] = None
    except Exception as exc:                      # noqa: BLE001 — any decode failure
        first_error = exc

    try:
        y, out_sr = _ffmpeg_load(file_path, sr, mono, offset, duration)
    except AudioLoadError:
        # ffmpeg is the fallback; if librosa had a real reason, that one is more
        # informative than "ffmpeg could not decode it".
        if first_error is not None:
            raise AudioLoadError(str(first_error)) from first_error
        raise
    if first_error is not None:
        log.debug("Decoded %s with ffmpeg (libsndfile refused it: %s)",
                  Path(file_path).name, first_error)
    return y, out_sr
