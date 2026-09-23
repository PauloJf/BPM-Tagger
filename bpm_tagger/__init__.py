"""BPM Tagger package.

Legacy re-exports: the codebase historically imported everything from the
monolithic ``bpm_tagger`` module. These names are re-exported here so existing
imports (and tests) keep working after the split into submodules.

They resolve lazily (PEP 562), so ``import bpm_tagger`` loads nothing beyond
this file — in particular not the optional layers (Navidrome, ntfy) that some
of these names live in. See docs/plans/core-decoupling.md.
"""

import importlib

# name -> submodule (relative to this package) that defines it
_EXPORTS = {
    "__version__": ".config",
    "AUDIO_EXTENSIONS": ".config",
    "build_config": ".config",
    "load_settings_override": ".config",
    "save_settings": ".config",
    "settings_file_path": ".config",
    "BPMDatabase": ".db",
    "ScanProgress": ".bpm.pipeline",
    "detect_bpm": ".bpm.pipeline",
    "_reconcile": ".bpm.pipeline",
    "_normalize_bpm": ".bpm.pipeline",
    "get_file_hash": ".bpm.tags",
    "write_bpm_tag": ".bpm.tags",
    "compute_waveform_peaks": ".bpm.waveform",
    "NotificationManager": ".notify.ntfy",
    "_trigger_navidrome_rescan": ".integrations.navidrome",
    "BPMTagger": ".scan.scanner",
    "_build_reasons": ".scan.scanner",
    "main": ".main",
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    mod = _EXPORTS.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(mod, __name__), name)
    globals()[name] = value  # cache: later lookups skip __getattr__
    return value


def __dir__():
    return sorted(set(globals()) | set(_EXPORTS))
