"""Application state container.

Replaces the ~14 module-level globals that the monolithic web_ui carried. A
single AppState instance lives on ``app.extensions["state"]`` and is reached
through the ``state()`` accessor. This is the only behavioural-risk change of
the M0 refactor, so it is kept faithful to the original semantics.
"""

import os
from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock

from flask import abort, current_app


@dataclass
class AppState:
    db: object = None
    music_dir: str = ""
    write_tags: bool = True
    preserve_mtime: bool = True
    conf_threshold: float = 0.4
    progress: object = None
    bpm_min: float = 60.0
    bpm_max: float = 200.0
    tagger: object = None
    config: dict = field(default_factory=dict)
    settings_path: str = ""
    restarting: bool = False

    # Waveform peak cache: path -> {peaks, duration}; insertion-ordered for eviction.
    waveform_cache: dict = field(default_factory=dict)
    waveform_cache_max: int = 500            # evict oldest 10% when exceeded
    waveform_inflight: dict = field(default_factory=dict)  # path -> Event; dedupe compute
    waveform_inflight_lock: Lock = field(default_factory=Lock)

    # Brute-force login protection
    login_attempts: defaultdict = field(default_factory=lambda: defaultdict(list))
    login_lockout_until: defaultdict = field(default_factory=lambda: defaultdict(float))
    login_lock: Lock = field(default_factory=Lock)
    max_login_attempts: int = 5
    lockout_seconds: int = 300
    attempt_window: int = 60

    def cache_waveform(self, path: str, result: dict) -> None:
        self.waveform_cache[path] = result
        if len(self.waveform_cache) > self.waveform_cache_max:
            evict = list(self.waveform_cache.keys())[:self.waveform_cache_max // 10]
            for k in evict:
                self.waveform_cache.pop(k, None)


def state() -> AppState:
    """Return the AppState bound to the current Flask app."""
    return current_app.extensions["state"]


def _assert_in_music_dir(file_path: str) -> str:
    real = os.path.realpath(file_path)
    music_real = os.path.realpath(state().music_dir)
    if not (real == music_real or real.startswith(music_real + os.sep)):
        abort(403)
    return real
