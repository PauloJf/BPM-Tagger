"""BPM reconciliation, plausibility normalization, and the public detect_bpm entry point."""

import logging
import threading
from pathlib import Path
from typing import Optional

from .detectors import (
    _detect_bpm_deeprhythm,
    _detect_bpm_essentia,
    _detect_bpm_librosa_multiseg,
    _librosa_window,
)

log = logging.getLogger(__name__)


class ScanProgress:
    STEPS = ("deeprhythm", "essentia", "librosa")

    def __init__(self):
        self._lock = threading.Lock()
        self.cumulative_completed = 0
        self._reset()

    def _reset(self):
        self.is_scanning  = False
        self.is_paused    = False
        self.is_stopping  = False
        self.current_file = ""
        self.current_step = ""
        self.completed    = 0
        self.total        = 0
        self.last_file    = ""
        self.last_bpm     = None

    def set_paused(self, paused: bool):
        with self._lock:
            self.is_paused = paused

    def set_stopping(self, stopping: bool):
        with self._lock:
            self.is_stopping = stopping

    def start(self, total: int):
        with self._lock:
            self._reset(); self.is_scanning = True; self.total = total

    def set_file(self, path: str):
        with self._lock:
            self.current_file = Path(path).name; self.current_step = ""

    def set_step(self, step: str):
        with self._lock:
            self.current_step = step

    def finish_file(self, path: str, bpm: Optional[float]):
        with self._lock:
            self.completed += 1
            self.cumulative_completed += 1
            self.last_file = Path(path).name; self.last_bpm = bpm
            self.current_file = ""; self.current_step = ""

    def finish(self):
        with self._lock:
            self.is_scanning = False; self.is_stopping = False
            self.current_file = ""; self.current_step = ""

    def snapshot(self) -> dict:
        with self._lock:
            si = (self.STEPS.index(self.current_step) + 1
                  if self.current_step in self.STEPS else 0)
            return dict(is_scanning=self.is_scanning, is_paused=self.is_paused,
                        is_stopping=self.is_stopping,
                        current_file=self.current_file,
                        current_step=self.current_step, step_index=si,
                        step_total=len(self.STEPS), completed=self.completed,
                        total=self.total, cumulative_completed=self.cumulative_completed,
                        last_file=self.last_file, last_bpm=self.last_bpm)


# ---------------------------------------------------------------------------
# Reconciliation + plausibility
# ---------------------------------------------------------------------------

def _reconcile(bpm_dr: Optional[float], bpm_es: Optional[float],
               bpm_lb: float, config: dict) -> tuple[float, bool]:
    """Return (final_bpm, needs_review) from deeprhythm, essentia, and librosa values."""
    threshold = config["review_disagree_threshold"]
    bpm_min, bpm_max = config["bpm_min"], config["bpm_max"]
    center = (bpm_min + bpm_max) / 2

    def _close(a: float, b: float) -> bool:
        return abs(a - b) <= threshold

    def _is_octave(a: float, b: float) -> bool:
        if a <= 0 or b <= 0:
            return False
        return 1.9 < max(a, b) / min(a, b) < 2.1

    def _fix_octave(a: float, b: float) -> float:
        for v in (a, b):
            if bpm_min <= v <= bpm_max:
                return v
        return min(a, b, key=lambda x: abs(x - center))

    # Both neural detectors available — primary path
    if bpm_dr is not None and bpm_es is not None:
        if config["octave_correction"] and _is_octave(bpm_dr, bpm_es):
            return _fix_octave(bpm_dr, bpm_es), False
        if _close(bpm_dr, bpm_es):
            return round((bpm_dr + bpm_es) / 2, 1), False
        # Detectors disagree — use librosa as tiebreaker, flag for review
        if bpm_lb > 0:
            chosen = bpm_dr if abs(bpm_dr - bpm_lb) <= abs(bpm_es - bpm_lb) else bpm_es
        else:
            chosen = bpm_dr
        return chosen, True

    # Only deeprhythm
    if bpm_dr is not None:
        if bpm_lb <= 0:
            return bpm_dr, True
        if config["octave_correction"] and _is_octave(bpm_dr, bpm_lb):
            return _fix_octave(bpm_dr, bpm_lb), False
        return bpm_dr, not _close(bpm_dr, bpm_lb)

    # Only essentia
    if bpm_es is not None:
        if bpm_lb <= 0:
            return bpm_es, True
        if config["octave_correction"] and _is_octave(bpm_es, bpm_lb):
            return _fix_octave(bpm_es, bpm_lb), False
        return bpm_es, not _close(bpm_es, bpm_lb)

    # Librosa only — both neural detectors failed
    return bpm_lb, True


def _normalize_bpm(bpm: float, bpm_min: float, bpm_max: float) -> float:
    """Halve/double until BPM is inside [bpm_min, bpm_max]."""
    if bpm <= 0 or bpm_min <= 0 or bpm_max <= 0 or bpm_min >= bpm_max:
        return round(bpm, 1)
    for _ in range(64):
        if bpm >= bpm_min:
            break
        bpm *= 2
    for _ in range(64):
        if bpm <= bpm_max:
            break
        bpm /= 2
    return round(bpm, 1)


# ---------------------------------------------------------------------------
# Public BPM detection entry point
# ---------------------------------------------------------------------------

def detect_bpm(file_path: str, config: dict, progress: Optional[ScanProgress] = None) -> dict:
    """
    Return dict with keys: bpm, bpm_dr, bpm_es, bpm_lb, confidence, detector, needs_review.
    Runs deeprhythm + essentia as primary detectors; librosa as confidence/tiebreaker.
    """
    n_seg = int(config.get("multi_segment_count", 3))
    seg_dur = float(config.get("segment_duration", 45))

    bpm_dr: Optional[float] = None
    if config.get("use_deeprhythm", True):
        if progress: progress.set_step("deeprhythm")
        try:
            bpm_dr = _detect_bpm_deeprhythm(file_path)
        except Exception as exc:
            log.warning("deeprhythm failed for %s: %s", Path(file_path).name, exc)

    bpm_es: Optional[float] = None
    if config.get("use_essentia", True):
        if progress: progress.set_step("essentia")
        bpm_es = _detect_bpm_essentia(file_path)  # handles exceptions internally

    if progress: progress.set_step("librosa")
    if config.get("multi_segment", True):
        bpm_lb, conf_lb = _detect_bpm_librosa_multiseg(file_path, n_seg, seg_dur)
    else:
        bpm_lb, conf_lb = _librosa_window(file_path, 0.0, 180.0)

    bpm_final, needs_review = _reconcile(bpm_dr, bpm_es, bpm_lb, config)

    if bpm_dr is not None and bpm_es is not None:
        detector = "deeprhythm+essentia"
    elif bpm_dr is not None:
        detector = "deeprhythm+librosa"
    elif bpm_es is not None:
        detector = "essentia+librosa"
    else:
        detector = "librosa"

    bpm_final = _normalize_bpm(bpm_final, config["bpm_min"], config["bpm_max"])

    return {
        "bpm":          bpm_final,
        "bpm_dr":       bpm_dr,
        "bpm_es":       bpm_es,
        "bpm_lb":       bpm_lb,
        "confidence":   conf_lb,
        "detector":     detector,
        "needs_review": needs_review,
    }
