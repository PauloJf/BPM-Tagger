"""BPM detection subsystem.

Third-party warning filters are registered here so they are active before any
librosa-importing submodule (detectors, waveform) runs:
- libsndfile can't decode MP3; librosa falls back to audioread (ffmpeg) automatically
- audioread fallback is deprecated in librosa 0.10 but still works until 1.0
"""

import warnings

warnings.filterwarnings("ignore", message="PySoundFile failed")
warnings.filterwarnings("ignore", category=FutureWarning, module="librosa")
warnings.filterwarnings("ignore", message="Using padding='same' with even kernel lengths")
