"""BPM detection subsystem.

Third-party warning filters are registered here so they are active before any
librosa-importing submodule (detectors, waveform) runs.

Decoding note: libsndfile handles wav/flac/ogg/opus/mp3 but not AAC. librosa
used to cover the gap by falling back to audioread (and so ffmpeg); that
fallback was deprecated in 0.10 and **removed in 1.0**, which silently broke
every .m4a and .aac once the unpinned `librosa>=0.10.0` rolled over. The
fallback now lives in `bpm.audio.load_audio`, which every caller uses instead
of `librosa.load` — so the version no longer decides which formats work.
"""

import warnings

warnings.filterwarnings("ignore", message="PySoundFile failed")
warnings.filterwarnings("ignore", category=FutureWarning, module="librosa")
warnings.filterwarnings("ignore", message="Using padding='same' with even kernel lengths")
