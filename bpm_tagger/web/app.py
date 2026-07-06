"""App factory + Waitress entry point.

``start(config, progress, tagger)`` keeps the exact signature the monolith
exposed and is still launched as a daemon thread from ``main()``.
"""

import logging
import os
import secrets
from datetime import timedelta
from pathlib import Path

from flask import Flask, request

from ..db import BPMDatabase
from .api.media import media_bp
from .api.scan import scan_bp
from .api.settings import settings_bp
from .api.stats import stats_bp
from .api.tracks import tracks_bp
from .auth import _csrf_token, auth_bp
from .pages import pages_bp
from .state import AppState

log = logging.getLogger(__name__)

# templates/ and static/ live at the repository/image root, above the package.
_ROOT = Path(__file__).resolve().parent.parent.parent
_TEMPLATE_DIR = str(_ROOT / "templates")
_STATIC_DIR = str(_ROOT / "static")

# Endpoints that must not force CSRF-token creation (see _ensure_csrf).
_CSRF_EXEMPT_ENDPOINTS = (None, "static", "media.healthz", "auth.login")


def create_app(config: dict) -> Flask:
    app = Flask(__name__, template_folder=_TEMPLATE_DIR, static_folder=_STATIC_DIR)
    app.jinja_env.filters["basename"] = lambda p: os.path.basename(p) if p else ""
    app.jinja_env.filters["dirname"]  = lambda p: os.path.dirname(p) if p else ""

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

    for bp in (auth_bp, pages_bp, tracks_bp, scan_bp, stats_bp, settings_bp, media_bp):
        app.register_blueprint(bp)

    @app.after_request
    def _security_headers(resp):
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; media-src 'self';"
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
    serve(app, host="0.0.0.0", port=port, threads=8)
