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
from datetime import timedelta
from pathlib import Path

from flask import Flask, abort, request, send_file, send_from_directory

from ..db import BPMDatabase
from .api.auth import api_auth_bp
from .api.media import media_bp
from .api.scan import scan_bp
from .api.playlists import playlists_bp
from .api.queue import queue_bp
from .api.settings import settings_bp
from .api.spotify import spotify_bp
from .api.stats import stats_bp
from .api.tracks import tracks_bp
from .auth import _csrf_token
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


def create_app(config: dict) -> Flask:
    app = Flask(__name__, static_folder=_STATIC_DIR)

    st = AppState()
    st.db = BPMDatabase(config["db_path"])
    st.music_dir = config["music_dir"]
    st.write_tags = config.get("write_tags", True)
    st.conf_threshold = config.get("review_confidence_threshold", 0.4)
    st.bpm_min = float(config.get("bpm_min", 60.0))
    st.bpm_max = float(config.get("bpm_max", 200.0))
    st.max_login_attempts = int(config.get("ui_max_login_attempts", 5))
    st.lockout_seconds = int(config.get("ui_lockout_seconds", 300))
    st.config = config
    st.settings_path = str(Path(config["db_path"]).parent / "settings.json")
    app.extensions["state"] = st

    secret = config.get("ui_secret_key") or secrets.token_hex(32)
    app.secret_key = secret
    app.config["UI_PASSWORD"] = config.get("ui_password", "")
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
        hours=int(config.get("ui_session_hours", 24))
    )
    app.config["SESSION_PERMANENT"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_HTTPONLY"] = True

    for bp in (api_auth_bp, tracks_bp, scan_bp, stats_bp, settings_bp, media_bp,
               spotify_bp, playlists_bp, queue_bp):
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
        if path and candidate.is_file() and str(candidate).startswith(str(_FRONTEND_DIST.resolve())):
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
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; img-src 'self' data:; media-src 'self';"
        )
        return resp

    @app.before_request
    def _ensure_csrf():
        if request.endpoint not in _CSRF_EXEMPT_ENDPOINTS:
            _csrf_token()

    return app


def start(config: dict, progress=None, tagger=None):
    password = config.get("ui_password", "")
    if not password:
        log.error("UI: UI_PASSWORD is not set — web UI will not start")
        return

    app = create_app(config)
    st = app.extensions["state"]
    st.progress = progress
    st.tagger = tagger

    from waitress import serve
    port = int(config.get("ui_port", 5000))
    log.info("BPM UI running on http://0.0.0.0:%d", port)
    serve(app, host="0.0.0.0", port=port, threads=12)
