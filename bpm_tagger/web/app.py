"""App factory + Waitress entry point.

``start(config, progress, tagger)`` keeps the exact signature the monolith
exposed and is still launched as a daemon thread from ``main()``.

Since M2 the browser UI is a React SPA built to ``frontend/dist``. Flask serves
that bundle: hashed assets under ``/assets``, the SPA's own static files, and an
``index.html`` catch-all for every non-API client route. The JSON API blueprints
are unchanged.
"""

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory, session
from flask.sessions import SecureCookieSessionInterface

from ..db import BPMDatabase
from .api.auth import api_auth_bp
from .api.images import images_bp
from .api.lyrics import lyrics_bp
from .api.media import media_bp
from .api.scan import scan_bp
from .api.inbox import inbox_bp
from .api.players import players_bp
from .api.playlists import playlists_bp
from .api.queue import queue_bp
from .api.run import run_bp
from .api.settings import settings_bp
from .api.spotify import spotify_bp
from .api.stats import stats_bp
from .api.suggestions import suggestions_bp
from .api.tracks import tracks_bp
from .auth import _csrf_token, password_stamp
from .state import AppState

log = logging.getLogger(__name__)

# static/ (fonts, favicon) lives at the repository/image root; the built SPA
# lives in frontend/dist next to it.
_ROOT = Path(__file__).resolve().parent.parent.parent
_STATIC_DIR = str(_ROOT / "static")
_FRONTEND_DIST = _ROOT / "frontend" / "dist"

# Endpoints that must not force CSRF-token creation (see _ensure_csrf).
# The SPA obtains its token from /api/me, so /api/login and the static shell are
# exempt.
_CSRF_EXEMPT_ENDPOINTS = (None, "static", "media.healthz", "api_auth.api_login",
                          "spa", "spa_assets", "api_spotify.spotify_callback")

# Path prefixes owned by the backend — never served the SPA shell.
_API_PREFIXES = ("api/", "audio", "healthz", "static/", "assets/")

# Player-only ("Run-only") role scope. A session that logged in with the run
# password may reach ONLY these endpoints; every other API endpoint is 403'd by
# _enforce_player_scope. This is a DEFAULT-DENY allowlist: any endpoint added in
# future is automatically off-limits to players until deliberately listed here.
# The SPA shell / static assets (None, "static", "spa", "spa_assets") always
# load so the client router can bounce the player to /run.
_PLAYER_ALWAYS = {None, "static", "spa", "spa_assets"}
_PLAYER_ALLOWED = {
    # Auth / bootstrap
    "api_auth.api_me", "api_auth.api_login", "api_auth.api_logout",
    # Building and playing the run queue (+ the playlist sources it can draw from)
    "api_run.api_run_queue", "api_run.api_run_playlists",
    "media.audio", "media.healthz", "media.api_scrobble",
    # Now-playing display + the two allowed track flags (star / dislike)
    "api_tracks.api_track", "api_tracks.api_track_cover_get",
    "api_tracks.api_waveform", "api_tracks.api_track_star",
    "api_tracks.api_track_dislike",
    # Run presets/tolerances (returned filtered to run_* keys for players)
    "settings.api_settings_get",
}


class _RoleSessionInterface(SecureCookieSessionInterface):
    """Give the player ("Run-only") role a longer cookie lifetime than the admin.

    Flask's ``PERMANENT_SESSION_LIFETIME`` is app-wide, so per-role expiry is set
    here: a permanent player session expires ``RUN_SESSION_SECONDS`` from now
    (sliding, since the cookie is re-sent each request); every other session
    keeps Flask's default behaviour."""

    def get_expiration_time(self, app, session):
        if session.permanent and session.get("role") == "player":
            secs = int(app.config.get("RUN_SESSION_SECONDS") or 0)
            if secs > 0:
                return datetime.now(timezone.utc) + timedelta(seconds=secs)
        return super().get_expiration_time(app, session)


def create_app(config: dict) -> Flask:
    app = Flask(__name__, static_folder=_STATIC_DIR)
    app.session_interface = _RoleSessionInterface()

    st = AppState()
    st.db = BPMDatabase(config["db_path"])
    st.music_dir = config["music_dir"]
    st.write_tags = config.get("write_tags", True)
    st.preserve_mtime = config.get("preserve_mtime", True)
    st.conf_threshold = config.get("review_confidence_threshold", 0.4)
    st.bpm_min = float(config.get("bpm_min", 60.0))
    st.bpm_max = float(config.get("bpm_max", 200.0))
    st.max_login_attempts = int(config.get("ui_max_login_attempts", 5))
    st.lockout_seconds = int(config.get("ui_lockout_seconds", 300))
    st.account_max_login_attempts = int(config.get("ui_account_max_login_attempts", 15))
    st.global_max_login_attempts = int(config.get("ui_global_max_login_attempts", 50))
    st.global_lockout_seconds = int(config.get("ui_global_lockout_seconds", 60))
    st.config = config
    st.settings_path = str(Path(config["db_path"]).parent / "settings.json")
    app.extensions["state"] = st

    # settings.json from older versions stores the UI password in clear —
    # replace it with a hash on first boot (no-op afterwards).
    try:
        from ..config import migrate_plaintext_password
        migrate_plaintext_password(st.settings_path, config)
    except Exception as exc:  # pragma: no cover - best effort
        log.warning("UI: password-hash migration failed: %s", exc)

    # A stable secret key keeps sessions valid across restarts (including the
    # in-place /api/restart). When none is configured, generate one once and
    # persist it so users aren't silently logged out on every restart.
    secret = config.get("ui_secret_key")
    if not secret:
        secret = secrets.token_hex(32)
        config["ui_secret_key"] = secret
        try:
            from ..config import save_settings
            save_settings(st.settings_path, {"ui_secret_key": secret})
            log.info("UI: generated and persisted a UI_SECRET_KEY for stable sessions")
        except Exception as exc:  # pragma: no cover - best effort
            log.warning("UI: could not persist generated secret key: %s", exc)
    app.secret_key = secret
    app.config["UI_PASSWORD"] = config.get("ui_password", "")
    app.config["UI_PASSWORD_HASH"] = config.get("ui_password_hash", "")
    # Sessions carry this stamp; login_required rejects sessions minted under a
    # previous password, so a password change logs out every other device.
    app.config["PW_STAMP"] = password_stamp(
        app.config["UI_PASSWORD_HASH"], app.config["UI_PASSWORD"])
    # Player-only ("Run-only") password — a second credential whose sessions are
    # confined to the Run page by the player-scope gate below. Its stamp is None
    # when unset, so login_required never accepts a stale player session once the
    # password is cleared (mirrors PW_STAMP for the admin password).
    app.config["RUN_PASSWORD"] = config.get("run_password", "")
    app.config["RUN_PASSWORD_HASH"] = config.get("run_password_hash", "")
    _run_stamp_src = app.config["RUN_PASSWORD_HASH"] or app.config["RUN_PASSWORD"]
    app.config["RUN_PW_STAMP"] = (
        password_stamp(app.config["RUN_PASSWORD_HASH"], app.config["RUN_PASSWORD"])
        if _run_stamp_src else None)
    # Player session length (see _RoleSessionInterface). Clamped to a sane range.
    _run_days = max(1, min(365, int(config.get("run_session_days", 30) or 30)))
    app.config["RUN_SESSION_SECONDS"] = _run_days * 86400
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
        hours=int(config.get("ui_session_hours", 24))
    )
    app.config["SESSION_PERMANENT"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    # Mark the cookie Secure when the UI is served over HTTPS — detected from an
    # https UI_PUBLIC_URL, or forced via UI_FORCE_SECURE_COOKIE for a
    # TLS-terminating proxy that forwards plain http (and where the public URL
    # isn't set). Left off for plain-http/local use so login still works there.
    app.config["SESSION_COOKIE_SECURE"] = (
        str(config.get("ui_public_url") or "").lower().startswith("https://")
        or bool(config.get("ui_force_secure_cookie")))

    # Behind a reverse proxy the login lockout must key on the real client IP,
    # not the proxy's. Opt-in via UI_TRUSTED_PROXIES (= number of proxies) so a
    # directly exposed instance can't have its IP spoofed by a forged header.
    trusted = int(config.get("ui_trusted_proxies", 0) or 0)
    if trusted > 0:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=trusted, x_proto=trusted,
                                x_host=trusted)
        if not app.config["SESSION_COOKIE_SECURE"]:
            log.warning(
                "UI: running behind a proxy but the session cookie is not marked "
                "Secure — if TLS terminates at the proxy, set UI_PUBLIC_URL to your "
                "https origin (or UI_FORCE_SECURE_COOKIE=true) so the cookie is "
                "never sent over plain http")

    for bp in (api_auth_bp, tracks_bp, scan_bp, stats_bp, settings_bp, media_bp,
               spotify_bp, playlists_bp, queue_bp, inbox_bp, lyrics_bp, images_bp,
               run_bp, suggestions_bp, players_bp):
        app.register_blueprint(bp)

    # ── SPA serving ─────────────────────────────────────────────────────────
    @app.route("/assets/<path:filename>")
    def spa_assets(filename):
        return send_from_directory(_FRONTEND_DIST / "assets", filename)

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def spa(path):
        # Backend-owned paths are handled by their own routes; if we get here
        # for one, it doesn't exist.
        if path.startswith(_API_PREFIXES):
            abort(404)
        # Serve a real dist file when it exists (e.g. a bundled icon); otherwise
        # hand back index.html so the client router can take over.
        candidate = (_FRONTEND_DIST / path).resolve()
        if path and candidate.is_file() and \
                str(candidate).startswith(str(_FRONTEND_DIST.resolve()) + os.sep):
            return send_file(candidate)
        index = _FRONTEND_DIST / "index.html"
        if not index.is_file():
            return ("Frontend not built. Run `npm run build` in frontend/.", 501)
        return send_file(index)

    @app.after_request
    def _security_headers(resp):
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        # Vite emits external, hashed script bundles, so 'unsafe-inline' is no
        # longer needed for scripts. Inline styles are still used (React style
        # props), so style-src keeps 'unsafe-inline'.
        # Spotify serves album/playlist art from its image CDNs, and the image
        # picker shows Deezer candidates from dzcdn; allow those hosts so covers
        # render in the UI (default-src stays same-origin). 30-second track
        # previews stream from Deezer's cdns-preview-*.dzcdn.net hosts, so
        # media-src allows *.dzcdn.net too.
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; "
            "img-src 'self' data: https://i.scdn.co https://*.scdn.co "
            "https://*.spotifycdn.com https://*.dzcdn.net; "
            "media-src 'self' https://*.dzcdn.net;"
        )
        return resp

    @app.before_request
    def _ensure_csrf():
        if request.endpoint not in _CSRF_EXEMPT_ENDPOINTS:
            _csrf_token()

    @app.before_request
    def _enforce_player_scope():
        # Confine run-only sessions to the Run page's endpoints. SPA/static
        # routes still load (so the client router can redirect to /run); any
        # other API endpoint is refused. Admin sessions are unaffected.
        if session.get("role") != "player":
            return
        ep = request.endpoint
        if ep in _PLAYER_ALWAYS or ep in _PLAYER_ALLOWED:
            return
        return jsonify(error="forbidden"), 403

    return app


def _weak_env_password(config: dict) -> bool:
    """True when the admin password is a plaintext env value (no stored hash)
    shorter than 8 chars. The UI change-password flow already enforces the
    minimum; this only catches the env fallback."""
    pw = config.get("ui_password") or ""
    return bool(pw and not config.get("ui_password_hash") and len(pw) < 8)


def start(config: dict, progress=None, tagger=None):
    if not (config.get("ui_password") or config.get("ui_password_hash")):
        log.error("UI: UI_PASSWORD is not set — web UI will not start")
        return

    # Warn (don't refuse — refusing would lock out an existing install on
    # upgrade) on a weak env password.
    if _weak_env_password(config):
        log.warning("UI: UI_PASSWORD is shorter than 8 characters — set a longer "
                    "one, or change it in Settings (which stores a hash and drops "
                    "the plaintext value)")

    app = create_app(config)
    st = app.extensions["state"]
    st.progress = progress
    st.tagger = tagger

    from waitress import serve
    port = int(config.get("ui_port", 5000))
    log.info("BPM UI running on http://0.0.0.0:%d", port)
    serve(app, host="0.0.0.0", port=port, threads=12)
