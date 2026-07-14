"""Auth helpers: CSRF tokens, brute-force lockout, login_required.

Since the React SPA replaced the Jinja UI (M2), the browser-facing login/logout
routes live in ``api/auth.py`` (JSON). This module now only holds the shared
helpers those routes and the protected API endpoints depend on.
"""

import hashlib
import hmac
import logging
import secrets

from flask import abort, current_app, jsonify, request, session
from functools import wraps

log = logging.getLogger(__name__)


def _csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


def verify_ui_password(candidate: str) -> bool:
    """Check a login/current-password attempt.

    A stored hash (set once the password is changed from the UI) is
    authoritative; the plaintext UI_PASSWORD env var is the fallback for
    fresh installs that never changed it.
    """
    hashed = current_app.config.get("UI_PASSWORD_HASH", "")
    if hashed:
        from werkzeug.security import check_password_hash
        try:
            return check_password_hash(hashed, candidate)
        except Exception:
            return False
    expected = current_app.config.get("UI_PASSWORD", "")
    return bool(expected) and hmac.compare_digest(candidate, expected)


def password_stamp(hashed: str, plain: str) -> str:
    """A short opaque value tied to the current password. Stored in every
    session at login and checked by login_required, so changing the password
    invalidates all previously issued sessions."""
    return hashlib.sha256(f"pw:{hashed or plain}".encode()).hexdigest()[:16]


def _check_csrf():
    token = (request.form.get("csrf_token")
             or request.headers.get("X-CSRF-Token", ""))
    stored = session.get("csrf_token", "")
    if not token or not stored or not hmac.compare_digest(token, stored):
        abort(403)


def login_required(f):
    """Protect an API endpoint. Returns 401 JSON when unauthenticated so the SPA
    can react (redirect to its own /login) instead of following an HTML redirect.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("ok") or \
                session.get("pw") != current_app.config.get("PW_STAMP"):
            return jsonify(error="unauthorized"), 401
        return f(*args, **kwargs)
    return wrapper
