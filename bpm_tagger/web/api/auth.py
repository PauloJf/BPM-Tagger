"""JSON auth endpoints for the React SPA (login / logout / me).

These mirror the session, CSRF and per-IP lockout semantics of the Jinja
``auth`` blueprint but speak JSON so the SPA can drive them with
``fetch(..., {credentials: 'same-origin'})`` + an ``X-CSRF-Token`` header. The
legacy form routes (``/login`` / ``/logout``) stay intact for the M0 Jinja UI.
"""

import hmac
import logging
import time

from flask import Blueprint, current_app, jsonify, request, session

from ...config import __version__
from ..auth import _check_csrf, _csrf_token
from ..state import state

log = logging.getLogger(__name__)

api_auth_bp = Blueprint("api_auth", __name__)


@api_auth_bp.route("/api/login", methods=["POST"])
def api_login():
    """Authenticate via JSON. Reuses the same lockout counters as the form login."""
    st = state()
    data = request.get_json(force=True, silent=True) or {}
    password = data.get("password", "")
    ip = request.remote_addr or "unknown"
    now = time.time()
    with st.login_lock:
        if now < st.login_lockout_until[ip]:
            return jsonify(ok=False, error="locked_out"), 429
        attempts = [t for t in st.login_attempts[ip] if now - t < st.attempt_window]
        st.login_attempts[ip] = attempts
        if len(attempts) >= st.max_login_attempts:
            st.login_lockout_until[ip] = now + st.lockout_seconds
            st.login_attempts[ip] = []
            return jsonify(ok=False, error="locked_out"), 429
        expected = current_app.config["UI_PASSWORD"]
        if password and expected and hmac.compare_digest(password, expected):
            st.login_attempts.pop(ip, None)
            st.login_lockout_until.pop(ip, None)
            session["ok"] = True
            return jsonify(ok=True, csrf_token=_csrf_token())
        st.login_attempts[ip].append(now)
    return jsonify(ok=False, error="invalid_password"), 401


@api_auth_bp.route("/api/logout", methods=["POST"])
def api_logout():
    _check_csrf()
    session.clear()
    return jsonify(ok=True)


@api_auth_bp.route("/api/me")
def api_me():
    """Report auth state + a few globals the SPA shell needs on boot.

    Not ``login_required``: an unauthenticated SPA calls this first to learn it
    must show the login screen. A fresh CSRF token is always minted so the
    login POST can be sent immediately.
    """
    st = state()
    authenticated = bool(session.get("ok"))
    resp = {
        "authenticated": authenticated,
        "version": __version__,
        "csrf_token": _csrf_token(),
    }
    if authenticated and st.db is not None:
        try:
            resp["review_count"] = st.db.get_stats().get("needs_review", 0)
        except Exception:
            resp["review_count"] = 0
    else:
        resp["review_count"] = 0
    return jsonify(resp)
