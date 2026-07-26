"""Perceived-loudness measurement and ReplayGain tag reading.

What gets stored is always **integrated loudness in LUFS** (ITU-R BS.1770 /
EBU R128) — the neutral fact about the file. The playback target is applied at
playback time (see `gain_db_for`), so changing the target later doesn't
invalidate anything already measured.

Two ways to get a value, cheapest first:

1. `read_loudness_tag()` — many libraries are already ReplayGain-tagged by
   beets / Picard / foobar2000 / loudgain. That's a header read, no decode, so
   it's tried first and a hit skips measurement entirely.
2. `measure_loudness()` — a real BS.1770 gated measurement via pyloudnorm.
   Costs one decode, so the scanner does it while the file is still warm in the
   page cache, alongside the waveform.
"""

import logging
from pathlib import Path
from typing import Optional

import librosa
import mutagen
import numpy as np
from mutagen.id3 import ID3
from mutagen.mp4 import MP4Tags

log = logging.getLogger(__name__)

# ReplayGain 2.0 reference loudness — what loudgain, Picard, beets and foobar2000
# all measure against, so `replaygain_track_gain` dB + this = the file's LUFS.
# (ReplayGain 1.0 used a different reference and no gated BS.1770 measurement; its
# tags are indistinguishable from RG2 in the file, so they read ~5 LU off. Close
# enough for levelling, and a rescan replaces the value with a real measurement.)
REPLAYGAIN_REF_LUFS = -18.0

# Opus carries its own `R128_TRACK_GAIN` header instead: a Q7.8 fixed-point
# integer relative to the EBU R128 reference.
R128_REF_LUFS = -23.0

# Loudness is measured on a downsampled stereo copy. 22.05 kHz keeps both
# K-weighting filters (a ~1681 Hz high shelf and a 38 Hz high-pass) far from
# Nyquist and preserves the channel information BS.1770 sums with per-channel
# weights — mono-averaging first would under-report wide-stereo material by up
# to 3 LU. Halving the rate also halves peak memory, which matters with several
# scan workers decoding full files at once.
MEASURE_SR = 22050

# BS.1770 gating uses 400 ms blocks; anything shorter can't be measured.
_MIN_SECONDS = 0.5


def _first(v):
    if isinstance(v, (list, tuple)):
        return v[0] if v else None
    return v


def _parse_gain_db(raw) -> Optional[float]:
    """Parse a ReplayGain gain value: '-7.50 dB', '+2.3dB', '-7.5' → float dB."""
    s = _first(raw)
    if s is None:
        return None
    if isinstance(s, bytes):
        s = s.decode("utf-8", "ignore")
    s = str(s).strip().lower().removesuffix("db").strip()
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def read_loudness_tag(file_path: str) -> Optional[float]:
    """Integrated loudness in LUFS from an existing ReplayGain tag, or None.

    Header read only — no decode. Handles ID3 TXXX (MP3/WAV/AIFF), Vorbis
    comments (FLAC/Ogg/Opus), MP4 freeform atoms, and Opus's R128 header.
    """
    try:
        audio = mutagen.File(file_path)
        tags = getattr(audio, "tags", None)
        if tags is None:
            return None

        if isinstance(tags, ID3):
            for frame in tags.getall("TXXX"):
                if str(getattr(frame, "desc", "")).lower() == "replaygain_track_gain":
                    gain = _parse_gain_db(frame.text)
                    if gain is not None:
                        return REPLAYGAIN_REF_LUFS + gain
            return None

        if isinstance(tags, MP4Tags):
            for key in ("----:com.apple.iTunes:replaygain_track_gain",
                        "----:com.apple.iTunes:REPLAYGAIN_TRACK_GAIN"):
                if key in tags:
                    gain = _parse_gain_db(tags[key])
                    if gain is not None:
                        return REPLAYGAIN_REF_LUFS + gain
            return None

        # Vorbis comments (and any other dict-like tag block). mutagen folds
        # Vorbis keys to lowercase, so a single lookup covers either casing.
        gain = _parse_gain_db(tags.get("replaygain_track_gain"))
        if gain is not None:
            return REPLAYGAIN_REF_LUFS + gain
        # Opus: Q7.8 fixed point, i.e. 256 units per dB.
        raw = _first(tags.get("r128_track_gain"))
        if raw is not None:
            try:
                return R128_REF_LUFS + int(str(raw).strip()) / 256.0
            except (ValueError, TypeError):
                pass
    except Exception as exc:
        log.debug("ReplayGain tag read failed for %s: %s", Path(file_path).name, exc)
    return None


def measure_loudness(file_path: str) -> Optional[float]:
    """Measure integrated loudness (LUFS) with a gated BS.1770 pass.

    Returns None if the file can't be decoded, is too short to gate, or is pure
    silence (which BS.1770 reports as -inf).
    """
    try:
        import pyloudnorm
    except ImportError:
        log.debug("pyloudnorm not installed — skipping loudness measurement")
        return None
    try:
        y, sr = librosa.load(file_path, sr=MEASURE_SR, mono=False)
        if y.size == 0:
            return None
        # librosa gives (channels, samples) for multichannel and (samples,) for
        # mono; pyloudnorm wants (samples,) or (samples, channels).
        data = y.T if y.ndim > 1 else y
        if len(data) < _MIN_SECONDS * sr:
            log.debug("Too short to measure loudness: %s", Path(file_path).name)
            return None
        lufs = float(pyloudnorm.Meter(sr).integrated_loudness(data))
        if not np.isfinite(lufs):
            return None            # silence, or gated away entirely
        return round(lufs, 2)
    except Exception as exc:
        log.warning("Loudness measurement failed for %s: %s", Path(file_path).name, exc)
        return None


def analyze_loudness(file_path: str, prefer_tag: bool = True) -> tuple[Optional[float], Optional[str]]:
    """Best available loudness for one file → (lufs, source).

    `source` is 'tag' when an existing ReplayGain tag supplied it and 'measured'
    when we decoded the file. Both are None when neither worked. With
    `prefer_tag=False` the file is always measured, which is what a "re-measure
    this track" action wants.
    """
    if prefer_tag:
        tagged = read_loudness_tag(file_path)
        if tagged is not None:
            return round(tagged, 2), "tag"
    measured = measure_loudness(file_path)
    return (measured, "measured") if measured is not None else (None, None)


def gain_db_for(lufs: Optional[float], target_lufs: float) -> float:
    """Playback gain in dB to bring a track to `target_lufs`.

    **Attenuation only** — the result is never positive. The player applies this
    by scaling `HTMLMediaElement.volume`, which is hard-clamped to [0, 1], so a
    boost is impossible without routing through Web Audio. Levelling downwards
    means loud tracks come down to meet the quiet ones, which needs no extra
    plumbing and can't clip. Unmeasured tracks (None) play untouched.
    """
    if lufs is None:
        return 0.0
    return min(0.0, target_lufs - lufs)
