"""Auth helpers: CSRF tokens, brute-force lockout, login_required.

Since the React SPA replaced the Jinja UI (M2), the browser-facing login/logout
routes live in ``api/auth.py`` (JSON). This module now only holds the shared
helpers those routes and the protected API endpoints depend on.
"""

import hmac
import logging
import secrets

from flask import abort, jsonify, request, session
from functools import wraps

log = logging.getLogger(__name__)


def _csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


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
        if not session.get("ok"):
            return jsonify(error="unauthorized"), 401
        return f(*args, **kwargs)
    return wrapper
