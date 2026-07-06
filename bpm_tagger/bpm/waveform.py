"""On-demand waveform peak computation for the UI."""

import json
import logging
from pathlib import Path
from typing import Optional

import librosa
import numpy as np

log = logging.getLogger(__name__)


def compute_waveform_peaks(file_path: str, n_bars: int = 300) -> Optional[str]:
    """Load audio at sr=2000 and return JSON-encoded peak data for waveform display.

    Returns None if loading fails. Intended to be called right after BPM analysis
    while the file is still warm in the OS page cache.
    """
    try:
        y, sr = librosa.load(file_path, sr=2000, mono=True)
        chunk = max(1, len(y) // n_bars)
        peaks: list[float] = []
        for i in range(n_bars):
            seg = y[i * chunk: (i + 1) * chunk]
            peaks.append(float(np.sqrt(np.mean(seg ** 2))) if len(seg) else 0.0)
        mx = max(peaks) or 1.0
        peaks = [round(p / mx, 4) for p in peaks]
        return json.dumps({"peaks": peaks, "duration": round(float(len(y) / sr), 3)})
    except Exception as exc:
        log.warning("Waveform computation failed for %s: %s", Path(file_path).name, exc)
        return None
