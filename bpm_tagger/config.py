"""Configuration: version discovery, env → config table, settings.json overrides."""

import json
import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".mp3", ".flac", ".ogg", ".m4a", ".aac", ".wav", ".opus", ".wv"}

# ── Anonymous install ping ────────────────────────────────────────────────────
# A one-time, opt-in "courtesy ping" so the author can gauge roughly how many
# installs exist. It carries ONLY the app version — no identifier, no library
# data, no usage data, no cookies. See bpm_tagger/install_ping.py.
#
# Delivered to GoatCounter's public pixel endpoint (GET /count?p=…), which needs
# no credential — the right fit for a beacon fired from installs we don't control
# (embedding any token would only be extractable from the public image, and a
# self-reported counter is inherently spoofable either way). GoatCounter is
# privacy-first and does not store IP addresses. Set this empty to make the whole
# feature dormant (no first-run prompt, nothing ever sent); override with the
# INSTALL_PING_URL env var to point it elsewhere or disable it.
INSTALL_PING_URL_DEFAULT = "https://bpmtagger.goatcounter.com/count"


def _parse_install_consent() -> "bool | None":
    """Tri-state install-ping consent from the INSTALL_PING env var.

    ``true`` / ``false`` preset the choice for headless installs (no UI to ask
    in); anything else leaves it unset so the UI asks on first run. settings.json
    overrides this once the user has answered."""
    raw = os.environ.get("INSTALL_PING", "").strip().lower()
    if raw in ("true", "1", "yes", "on"):
        return True
    if raw in ("false", "0", "no", "off"):
        return False
    return None

# Settings whose UI control is locked when the matching env var is explicitly
# set (e.g. in docker-compose). Maps env var name → config key. A locked key is
# authoritative from the environment: settings.json cannot override it and the
# web UI cannot change it.
_ENV_LOCKABLE = {
    "PRESERVE_MTIME": "preserve_mtime",
}


def env_locked_keys() -> list:
    """Config keys whose env var is explicitly present — locked from UI edits."""
    return [cfg_key for env, cfg_key in _ENV_LOCKABLE.items() if env in os.environ]


def _read_version() -> str:
    # VERSION lives at the repository / image root, one directory above this package.
    root = Path(__file__).resolve().parent.parent
    vf = root / "VERSION"
    if vf.is_file():
        v = vf.read_text().strip()
        if v:
            return v.lstrip("v")
    # Fall back to git tag when running directly from a git checkout
    try:
        v = subprocess.check_output(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=str(root),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if v:
            return v.lstrip("v")
    except Exception:
        pass
    return "dev"


__version__ = _read_version()


def read_changelog() -> str:
    """Return the bundled CHANGELOG.md text (empty string if not present). Lives
    at the repository / image root alongside VERSION; served to the admin UI for
    the 'What's new' popup and the changelog view."""
    root = Path(__file__).resolve().parent.parent
    cf = root / "CHANGELOG.md"
    try:
        return cf.read_text(encoding="utf-8") if cf.is_file() else ""
    except Exception:
        return ""


RUN_PRESET_DEFAULTS = [("Warmup", 120), ("Easy", 155), ("Steady", 165), ("Tempo", 175)]


def _parse_run_presets(raw: str) -> list:
    """``"Name:bpm,Name:bpm,..."`` (bare ``bpm`` allowed) → exactly 4
    ``{name, bpm}`` presets, padded from the defaults."""
    out = []
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        name, _, val = part.rpartition(":")
        try:
            bpm = max(30, min(300, int(float(val or part))))
        except (ValueError, TypeError):
            continue
        dflt_name = RUN_PRESET_DEFAULTS[min(len(out), 3)][0]
        out.append({"name": (name.strip() or dflt_name)[:20], "bpm": bpm})
    out = out[:4]
    return out + [{"name": n, "bpm": b} for n, b in RUN_PRESET_DEFAULTS[len(out):]]


def settings_file_path(db_path: str) -> str:
    return str(Path(db_path).parent / "settings.json")


def load_settings_override(config: dict) -> dict:
    """Merge persisted settings.json overrides into config (overwrites env-var values)."""
    path = settings_file_path(config["db_path"])
    if os.path.isfile(path):
        try:
            with open(path) as f:
                data = json.load(f)
            # Env-locked keys stay authoritative from the environment.
            for key in env_locked_keys():
                data.pop(key, None)
            config.update(data)
        except Exception as exc:
            log.warning("Could not load settings override: %s", exc)
    return config


def save_settings(settings_path: str, updates: dict) -> None:
    """Merge updates into settings.json at settings_path (creating it if absent).

    A value of ``None`` removes the key. The file is created/kept at 0600 —
    it holds secrets (Navidrome password, Deezer ARL, the session secret key).
    """
    existing = {}
    if os.path.isfile(settings_path):
        try:
            with open(settings_path) as f:
                existing = json.load(f)
        except Exception:
            pass
    for key, val in updates.items():
        if val is None:
            existing.pop(key, None)
        else:
            existing[key] = val
    fd = os.open(settings_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(existing, f, indent=2)
    try:  # pre-existing file keeps its old mode — tighten it too
        os.chmod(settings_path, 0o600)
    except OSError:
        pass


def migrate_plaintext_password(settings_path: str, config: dict) -> None:
    """One-time migration: settings.json used to store the UI password in clear.

    Replace it with a werkzeug hash (``ui_password_hash``) and drop the
    plaintext key. The UI_PASSWORD env var is untouched — it stays the
    plaintext fallback until the password is first changed from the UI.
    """
    if not os.path.isfile(settings_path):
        return
    try:
        with open(settings_path) as f:
            stored = json.load(f)
    except Exception:
        return
    plain = stored.get("ui_password")
    if not plain:
        return
    from werkzeug.security import generate_password_hash
    hashed = generate_password_hash(plain)
    save_settings(settings_path, {"ui_password_hash": hashed, "ui_password": None})
    config["ui_password_hash"] = hashed
    config["ui_password"] = ""
    log.info("Migrated UI password in settings.json to a hash")


def build_config() -> dict:
    """Build the config table from environment variables (env = defaults)."""
    raw_ext = os.environ.get("AUDIO_EXTENSIONS", ",".join(AUDIO_EXTENSIONS))
    extensions = {e.strip().lower() for e in raw_ext.split(",") if e.strip()}

    return {
        "music_dir":                  os.environ.get("MUSIC_DIR", "/music"),
        "db_path":                    os.environ.get("DB_PATH", "/data/bpm_tagger.db"),
        "ntfy_url":                   os.environ.get("NTFY_URL", ""),
        "ntfy_topic":                 os.environ.get("NTFY_TOPIC", ""),
        "ntfy_batch_size":            int(os.environ.get("NTFY_BATCH_SIZE", "10")),
        "ntfy_min_interval":          int(os.environ.get("NTFY_MIN_INTERVAL", "300")),
        "ntfy_notify_review":         os.environ.get("NTFY_NOTIFY_REVIEW", "true").lower() == "true",
        "write_tags":                 os.environ.get("WRITE_TAGS", "true").lower() == "true",
        "preserve_mtime":             os.environ.get("PRESERVE_MTIME", "true").lower() == "true",
        "extensions":                 extensions,
        "bpm_min":                    float(os.environ.get("BPM_MIN", "60")),
        "bpm_max":                    float(os.environ.get("BPM_MAX", "200")),
        "octave_correction":          os.environ.get("OCTAVE_CORRECTION", "true").lower() == "true",
        "multi_segment":              os.environ.get("MULTI_SEGMENT", "true").lower() == "true",
        "multi_segment_count":        int(os.environ.get("MULTI_SEGMENT_COUNT", "3")),
        "segment_duration":           float(os.environ.get("SEGMENT_DURATION", "45")),
        "review_confidence_threshold":float(os.environ.get("REVIEW_CONFIDENCE_THRESHOLD", "0.4")),
        "review_disagree_threshold":  float(os.environ.get("REVIEW_DISAGREE_THRESHOLD", "15")),
        "use_deeprhythm":             os.environ.get("USE_DEEPRHYTHM", "false").lower() == "true"
                                      and os.environ.get("WITH_DEEPRHYTHM", "false").lower() == "true",
        "use_essentia":               os.environ.get("USE_ESSENTIA", "true").lower() == "true",
        "report_path":                os.environ.get("REPORT_PATH", "/data/review_report.csv"),
        "enable_ui":                  os.environ.get("ENABLE_UI", "false").lower() == "true",
        # ── Anonymous install ping (opt-in, one-time; see install_ping.py) ────
        "install_ping_url":           os.environ.get("INSTALL_PING_URL", INSTALL_PING_URL_DEFAULT),
        # None = not yet asked (UI prompts on first run); True/False = user's answer.
        "install_ping_consent":       _parse_install_consent(),
        # The app version last successfully pinged (persisted to settings.json).
        # A ping fires when this differs from the running version — i.e. on the
        # first opt-in and once more after each update.
        "install_ping_version":       "",
        "playback_buffer":            float(os.environ.get("PLAYBACK_BUFFER", "3")),
        # ── Run mode (tempo-locked playback) — normally edited from the UI ────
        "run_presets":                _parse_run_presets(os.environ.get(
            "RUN_PRESETS", "Warmup:120,Easy:155,Steady:165,Tempo:175")),
        "run_octave_fold":            os.environ.get("RUN_OCTAVE_FOLD", "true").lower() == "true",
        "run_prefer_starred":         os.environ.get("RUN_PREFER_STARRED", "true").lower() == "true",
        # Within a star tier, fill the queue most-played-first (play counts
        # pulled from Navidrome). Off = closest BPM first, as before.
        "run_prefer_familiar":        os.environ.get("RUN_PREFER_FAMILIAR", "false").lower() == "true",
        "run_queue_size":             int(os.environ.get("RUN_QUEUE_SIZE", "20")),
        "run_tolerance_pct":          float(os.environ.get("RUN_TOLERANCE_PCT", "4")),
        "run_stretch_limit_pct":      float(os.environ.get("RUN_STRETCH_LIMIT_PCT", "15")),
        # "Play everything, force tempo": ignore the tolerance filter and force every
        # candidate to the target via playbackRate (clamped for extreme outliers). The
        # default for a run when the request doesn't send an explicit force flag.
        "run_force_tempo":            os.environ.get("RUN_FORCE_TEMPO", "false").lower() == "true",
        "fetch_artist_images":        os.environ.get("FETCH_ARTIST_IMAGES", "false").lower() == "true",
        # Save fetched/picked artist images as artist.jpg in the artist's own
        # folder (Navidrome convention) instead of only the app cache. Only
        # writes when the layout has a folder exclusive to the artist.
        "artist_images_to_library":   os.environ.get("ARTIST_IMAGES_TO_LIBRARY", "false").lower() == "true",
        # Lyrics: lyrics_enabled gates the automatic fetch for grabbed tracks;
        # manual per-track fetch/edit in the UI is always available.
        "lyrics_enabled":             os.environ.get("LYRICS_ENABLED", "false").lower() == "true",
        "lyrics_mode":                os.environ.get("LYRICS_MODE", "embed"),  # embed | sidecar
        "ui_port":                    int(os.environ.get("UI_PORT", "5000")),
        "ui_password":                os.environ.get("UI_PASSWORD", ""),
        # Set (in settings.json) the first time the password is changed from the
        # UI; once present it is authoritative and ui_password is ignored.
        "ui_password_hash":           "",
        # Admin two-factor (TOTP). All UI-managed, persisted to settings.json:
        #   totp_enabled          — whether the admin login requires a code.
        #   totp_secret           — the base32 shared secret (a secret at rest,
        #                           same trust level as the password hash).
        #   totp_recovery_hashes  — werkzeug hashes of the unused one-time codes.
        # A lost authenticator is recoverable via a code or `MODE=disable_2fa`.
        "totp_enabled":               False,
        "totp_secret":                "",
        "totp_recovery_hashes":       [],
        "ui_secret_key":              os.environ.get("UI_SECRET_KEY", ""),
        # ── Player-only ("Run-only") access ──────────────────────────────────
        # A second password that logs into a restricted role able to reach ONLY
        # the Run page (tempo player). Empty = feature off, no second login.
        # Env sets the initial value; once changed from the UI the hash below is
        # authoritative (mirrors ui_password / ui_password_hash).
        "run_password":               os.environ.get("RUN_PASSWORD", ""),
        "run_password_hash":          "",
        # How long a player ("Run-only") login stays signed in, in days (sliding
        # — each use extends it). It's a low-privilege kiosk opened repeatedly
        # for runs, so it defaults far longer than the admin session.
        "run_session_days":           int(os.environ.get("RUN_SESSION_DAYS", "30")),
        # Number of reverse proxies in front of the UI (X-Forwarded-For depth).
        # Leave 0 when port 5000 is reached directly; set 1 behind nginx/traefik
        # so the login brute-force lockout keys on the real client IP.
        "ui_trusted_proxies":         int(os.environ.get("UI_TRUSTED_PROXIES", "0")),
        # Force the session cookie's Secure flag on even when UI_PUBLIC_URL isn't
        # an https origin — for a TLS-terminating reverse proxy that forwards
        # plain http. Leave off for direct http/local use, or login breaks there.
        "ui_force_secure_cookie":     os.environ.get("UI_FORCE_SECURE_COOKIE", "false").lower() == "true",
        "ui_session_hours":           int(os.environ.get("UI_SESSION_HOURS", "24")),
        "ui_max_login_attempts":      int(os.environ.get("UI_MAX_LOGIN_ATTEMPTS", "5")),
        "ui_lockout_seconds":         int(os.environ.get("UI_LOCKOUT_SECONDS", "300")),
        # Per-account (per-username, and the shared admin/guest key) backstop for
        # a distributed attack — higher than the per-IP cap so it doesn't let a
        # few hostile requests lock the single admin out from every IP.
        "ui_account_max_login_attempts": int(os.environ.get("UI_ACCOUNT_MAX_LOGIN_ATTEMPTS", "15")),
        # Global (all-IPs, all-accounts) brute-force backstop: a high threshold
        # with a short cooldown so a broad distributed sweep is slowed without a
        # legit crowd tripping it or an attacker cheaply DoSing the login.
        "ui_global_max_login_attempts": int(os.environ.get("UI_GLOBAL_MAX_LOGIN_ATTEMPTS", "50")),
        "ui_global_lockout_seconds":  int(os.environ.get("UI_GLOBAL_LOCKOUT_SECONDS", "60")),
        "workers":                    int(os.environ.get("WORKERS", "1")),
        "navidrome_url":              os.environ.get("NAVIDROME_URL", ""),
        "navidrome_user":             os.environ.get("NAVIDROME_USER", ""),
        "navidrome_pass":             os.environ.get("NAVIDROME_PASS", ""),
        # Two-way star sync (docs/plans/navidrome-star-sync.md): gates the
        # "Sync stars now" button in Settings. Manual trigger only in v1.
        "navidrome_star_sync":        os.environ.get("NAVIDROME_STAR_SYNC", "false").lower() == "true",
        # Scrobble plays from the built-in player to Navidrome (which forwards
        # to Last.fm/ListenBrainz when configured there).
        "navidrome_scrobble":         os.environ.get("NAVIDROME_SCROBBLE", "false").lower() == "true",
        # Background scheduler (Phase 5): minutes between automatic sync passes for
        # playlists (Spotify+Navidrome), stars, and play counts. 0 = off (manual only).
        # Floored to 5 min when non-zero. Runs only in the long-lived watch modes.
        "sync_interval_minutes":      int(os.environ.get("SYNC_INTERVAL_MINUTES", "0")),

        # ── Grabber (M3+) ─────────────────────────────────────────────────────
        "grabber_enabled":            os.environ.get("GRABBER_ENABLED", "false").lower() == "true",
        "index_tags":                 os.environ.get("INDEX_TAGS", "true").lower() == "true",
        # Spotify OAuth — client id/secret are env-only, never persisted to settings.json
        "spotify_client_id":          os.environ.get("SPOTIFY_CLIENT_ID", ""),
        "spotify_client_secret":      os.environ.get("SPOTIFY_CLIENT_SECRET", ""),
        "spotify_redirect_uri":       os.environ.get("SPOTIFY_REDIRECT_URI", ""),
        "spotify_sync_minutes":       int(os.environ.get("SPOTIFY_SYNC_MINUTES", "30")),
        "ui_public_url":              os.environ.get("UI_PUBLIC_URL", ""),
        # Providers / pipeline (used from M4; declared here so settings.json can hold them)
        "monochrome_base_url":        os.environ.get("MONOCHROME_BASE_URL", ""),
        "monochrome_api_key":         os.environ.get("MONOCHROME_API_KEY", ""),
        "monochrome_quality":         os.environ.get("MONOCHROME_QUALITY", "LOSSLESS"),
        # Deezer (streamrip): free-tier ARL yields full tracks at MP3 128 kbps;
        # MP3_320 / FLAC require a paid Deezer subscription. ARL is env/config only.
        "deezer_arl":                 os.environ.get("DEEZER_ARL", ""),
        "deezer_quality":             os.environ.get("DEEZER_QUALITY", "MP3_128"),
        "provider_order":             os.environ.get("PROVIDER_ORDER", "deezer,ytdlp"),
        "output_format":              os.environ.get("OUTPUT_FORMAT", "mp3-128"),
        "path_template":              os.environ.get(
            "PATH_TEMPLATE", "{AlbumArtist}/{Album}/{TrackNo:02d} - {Title}.{ext}"),
        "grab_workers":               int(os.environ.get("GRAB_WORKERS", "1")),
        "grab_dry_run":               os.environ.get("GRAB_DRY_RUN", "false").lower() == "true",
        "auto_accept_threshold":      float(os.environ.get("AUTO_ACCEPT_THRESHOLD", "0.85")),
        "ask_threshold":              float(os.environ.get("ASK_THRESHOLD", "0.55")),
    }
