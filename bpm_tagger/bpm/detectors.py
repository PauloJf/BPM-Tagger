"""Low-level BPM detectors: deeprhythm (CNN), essentia, librosa multi-segment."""

import logging
import threading
from pathlib import Path
from typing import Optional

import librosa
import mutagen
import numpy as np

from .audio import load_audio

log = logging.getLogger(__name__)

_local = threading.local()


def _get_predictor():
    if not hasattr(_local, "predictor"):
        log.info("Loading DeepRhythm model into memory…")
        from deeprhythm import DeepRhythmPredictor
        _local.predictor = DeepRhythmPredictor()
        log.info("DeepRhythm model ready")
    return _local.predictor


# DeepRhythm works on 8-second clips at 22.05 kHz and silently yields nothing
# from audio too short to fill one.
_DR_SR = 22050
_DR_CLIP_SECONDS = 8


def _detect_bpm_deeprhythm(file_path: str) -> Optional[float]:
    """BPM from the DeepRhythm CNN, or None when the file can't feed it.

    Decoding is done here rather than by DeepRhythm's own predict(), which calls
    librosa.load internally and so inherits the AAC gap that bpm.audio.load_audio
    exists to close. When that load failed, DeepRhythm returned None into a
    tensor call and surfaced it as "'NoneType' object has no attribute 'to'" —
    an error that says nothing about the real cause. Handing it samples via
    predict_from_audio() keeps every format the rest of the pipeline can read.
    """
    y, sr = load_audio(file_path, sr=_DR_SR, mono=True)
    if y.size < _DR_SR * _DR_CLIP_SECONDS:
        log.debug("Too short for deeprhythm (%.1fs < %ds): %s",
                  y.size / _DR_SR, _DR_CLIP_SECONDS, Path(file_path).name)
        return None
    bpm = _get_predictor().predict_from_audio(y, sr)
    return round(float(bpm), 1) if bpm else None


def _detect_bpm_essentia(file_path: str) -> Optional[float]:
    """Return BPM from essentia RhythmExtractor2013 (multifeature), or None on failure."""
    try:
        import essentia.standard as es
        audio = es.EasyLoader(filename=file_path, sampleRate=44100)()
        bpm, _, _, _, _ = es.RhythmExtractor2013(method="multifeature")(audio)
        return round(float(bpm), 1)
    except Exception as exc:
        log.warning("essentia failed for %s: %s", Path(file_path).name, exc)
        return None


def _track_duration(file_path: str) -> Optional[float]:
    """Return track length in seconds, or None if the format is unsupported."""
    audio = mutagen.File(file_path)
    if audio and hasattr(audio.info, "length") and audio.info.length > 0:
        return audio.info.length
    return None


def _librosa_window(file_path: str, offset: float, duration: float) -> tuple[float, float]:
    y, sr = load_audio(file_path, offset=offset, duration=duration, sr=None, mono=True)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, aggregate=np.median)
    candidates = librosa.feature.tempo(onset_envelope=onset_env, sr=sr, aggregate=None)
    bpm = float(np.median(candidates)) if len(candidates) > 0 else 0.0
    _, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, trim=False)
    if len(beats) > 2:
        intervals = np.diff(librosa.frames_to_time(beats, sr=sr))
        conf = float(np.clip(1.0 - np.std(intervals) / (np.mean(intervals) + 1e-9), 0.0, 1.0))
    else:
        conf = 0.0
    return round(bpm, 1), conf


def _detect_bpm_librosa_multiseg(file_path: str, n_segments: int, seg_duration: float) -> tuple[float, float]:
    total = _track_duration(file_path)

    if total is None or total < seg_duration * 1.5:
        return _librosa_window(file_path, 0.0, 180.0)

    offsets = [
        max(0.0, min(total * (i + 1) / (n_segments + 1) - seg_duration / 2, total - seg_duration))
        for i in range(n_segments)
    ]
    bpms, confs = zip(*[_librosa_window(file_path, o, seg_duration) for o in offsets])
    return round(float(np.median(bpms)), 1), float(np.median(confs))
