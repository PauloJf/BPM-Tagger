"""BPM Tagger package.

Legacy re-exports: the codebase historically imported everything from the
monolithic ``bpm_tagger`` module. These names are re-exported here so existing
imports (and tests) keep working after the split into submodules.
"""

from .config import (
    AUDIO_EXTENSIONS,
    __version__,
    build_config,
    load_settings_override,
    save_settings,
    settings_file_path,
)
from .db import BPMDatabase
from .bpm.pipeline import ScanProgress, _normalize_bpm, _reconcile, detect_bpm
from .bpm.tags import get_file_hash, write_bpm_tag
from .bpm.waveform import compute_waveform_peaks
from .integrations.navidrome import _trigger_navidrome_rescan
from .notify.ntfy import NotificationManager
from .scan.scanner import BPMTagger, _build_reasons
from .main import main

__all__ = [
    "__version__",
    "AUDIO_EXTENSIONS",
    "build_config",
    "load_settings_override",
    "save_settings",
    "settings_file_path",
    "BPMDatabase",
    "ScanProgress",
    "detect_bpm",
    "_reconcile",
    "_normalize_bpm",
    "get_file_hash",
    "write_bpm_tag",
    "compute_waveform_peaks",
    "NotificationManager",
    "_trigger_navidrome_rescan",
    "BPMTagger",
    "_build_reasons",
    "main",
]
