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
    waveform_cache_lock: Lock = field(default_factory=Lock)  # guards cache reads/eviction
    waveform_inflight: dict = field(default_factory=dict)  # path -> Event; dedupe compute
    waveform_inflight_lock: Lock = field(default_factory=Lock)

    # Brute-force login protection. Three independent layers, all guarded by
    # login_lock and evaluated per attempt (see login_locked):
    #   * per-IP     — the original layer; stops one host hammering the login.
    #   * per-account— stops a *distributed* attack on one identity (many IPs,
    #     one username/password), which the per-IP layer alone can't see. The
    #     "account" is the target the attempt names: a player username, or the
    #     shared "__shared__" key for a blank-username attempt (admin + guest).
    #   * global     — a coarse backstop against a broad sweep (many IPs, many
    #     accounts). Deliberately a HIGH threshold + a SHORT cooldown, so a
    #     legit crowd never trips it and an attacker can't cheaply DoS logins.
    login_attempts: defaultdict = field(default_factory=lambda: defaultdict(list))
    login_lockout_until: defaultdict = field(default_factory=lambda: defaultdict(float))
    account_attempts: defaultdict = field(default_factory=lambda: defaultdict(list))
    account_lockout_until: defaultdict = field(default_factory=lambda: defaultdict(float))
    global_attempts: list = field(default_factory=list)
    global_lockout_until: float = 0.0
    login_lock: Lock = field(default_factory=Lock)
    max_login_attempts: int = 5
    lockout_seconds: int = 300
    attempt_window: int = 60
    # Per-account uses a HIGHER threshold than per-IP on purpose. Its job is to
    # catch a *distributed* guessing attack (still far too few tries to crack a
    # decent password), not to hair-trigger on the shared admin/guest key — where
    # a low cap would let 5 fumbles (or 5 hostile requests) lock the single admin
    # out from every IP. Per-IP stays tight for the single-host case.
    account_max_login_attempts: int = 15
    global_max_login_attempts: int = 50
    global_lockout_seconds: int = 60

    def login_locked(self, ip: str, account: str, now: float) -> bool:
        """Whether this attempt should be refused (429) before checking the
        password. Call inside ``login_lock``. Checks the three active lockouts,
        then prunes each window and trips a fresh lockout if a threshold is
        already met by prior failures (mirrors the original per-IP semantics:
        the lockout trips on the attempt *after* the limit is reached)."""
        if now < self.global_lockout_until:
            return True
        if now < self.login_lockout_until[ip]:
            return True
        if now < self.account_lockout_until[account]:
            return True
        self.login_attempts[ip] = [t for t in self.login_attempts[ip] if now - t < self.attempt_window]
        if len(self.login_attempts[ip]) >= self.max_login_attempts:
            self.login_lockout_until[ip] = now + self.lockout_seconds
            self.login_attempts[ip] = []
            return True
        self.account_attempts[account] = [t for t in self.account_attempts[account] if now - t < self.attempt_window]
        if len(self.account_attempts[account]) >= self.account_max_login_attempts:
            self.account_lockout_until[account] = now + self.lockout_seconds
            self.account_attempts[account] = []
            return True
        self.global_attempts = [t for t in self.global_attempts if now - t < self.attempt_window]
        if len(self.global_attempts) >= self.global_max_login_attempts:
            self.global_lockout_until = now + self.global_lockout_seconds
            self.global_attempts = []
            return True
        return False

    def login_succeeded(self, ip: str, account: str) -> None:
        """Clear the per-IP and per-account counters on a successful login. The
        global counter is left to age out of its window — one success amid a
        broad attack shouldn't erase the signal from every other host."""
        self.login_attempts.pop(ip, None)
        self.login_lockout_until.pop(ip, None)
        self.account_attempts.pop(account, None)
        self.account_lockout_until.pop(account, None)

    def login_failed(self, ip: str, account: str, now: float) -> None:
        """Record a failed attempt against all three layers. Call inside login_lock."""
        self.login_attempts[ip].append(now)
        self.account_attempts[account].append(now)
        self.global_attempts.append(now)

    def clear_login_lockouts(self) -> None:
        """Drop all recorded attempts and active lockouts across every layer. For
        the admin "reset lockouts" action — clears transient state (e.g. a locked-
        out household member) WITHOUT changing any threshold, so it can't weaken
        the policy."""
        with self.login_lock:
            self.login_attempts.clear()
            self.login_lockout_until.clear()
            self.account_attempts.clear()
            self.account_lockout_until.clear()
            self.global_attempts.clear()
            self.global_lockout_until = 0.0

    def cache_waveform(self, path: str, result: dict) -> None:
        # Served by up to 12 Waitress threads: the insert + eviction-scan must be
        # atomic, or a concurrent write raises "dictionary changed size during
        # iteration" (and readers can observe torn state).
        with self.waveform_cache_lock:
            self.waveform_cache[path] = result
            if len(self.waveform_cache) > self.waveform_cache_max:
                evict = list(self.waveform_cache.keys())[:self.waveform_cache_max // 10]
                for k in evict:
                    self.waveform_cache.pop(k, None)

    def get_waveform(self, path: str):
        """Thread-safe cache read (avoids a check-then-get race with eviction)."""
        with self.waveform_cache_lock:
            return self.waveform_cache.get(path)


def state() -> AppState:
    """Return the AppState bound to the current Flask app."""
    return current_app.extensions["state"]


def _assert_in_music_dir(file_path: str) -> str:
    real = os.path.realpath(file_path)
    music_real = os.path.realpath(state().music_dir)
    if not (real == music_real or real.startswith(music_real + os.sep)):
        abort(403)
    return real
