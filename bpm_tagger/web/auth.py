"""Auth helpers: CSRF tokens, brute-force lockout, login_required.

Since the React SPA replaced the Jinja UI (M2), the browser-facing login/logout
routes live in ``api/auth.py`` (JSON). This module now only holds the shared
helpers those routes and the protected API endpoints depend on.
"""

import hashlib
import hmac
import logging
import secrets

from typing import Optional

from flask import abort, current_app, g, jsonify, request, session
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


def verify_run_password(candidate: str) -> bool:
    """Check a login attempt against the player-only ("Run-only") password.

    Mirrors ``verify_ui_password``: a stored hash (set once changed from the UI)
    is authoritative, else the plaintext ``RUN_PASSWORD`` env fallback. Returns
    False when no run password is configured, so the player role is simply
    unreachable until an admin sets one."""
    hashed = current_app.config.get("RUN_PASSWORD_HASH", "")
    if hashed:
        from werkzeug.security import check_password_hash
        try:
            return check_password_hash(hashed, candidate)
        except Exception:
            return False
    expected = current_app.config.get("RUN_PASSWORD", "")
    return bool(expected) and hmac.compare_digest(candidate, expected)


def verify_player(username: str, candidate: str) -> Optional[dict]:
    """Return the enabled player-user row whose username+password match, else None.

    Reuses werkzeug ``check_password_hash`` — the same primitive
    ``verify_ui_password`` / ``verify_run_password`` use. Usernames are stored
    lowercased; matching is therefore case-insensitive. Disabled users never match
    (an admin can lock a user out without deleting it)."""
    if not username or not candidate:
        return None
    # Local import avoids any import cycle between auth.py and web.state.
    from .state import state
    db = state().db
    if db is None:
        return None
    row = db.get_player_by_username(username)
    if not row or not row.get("enabled"):
        return None
    from werkzeug.security import check_password_hash
    try:
        if check_password_hash(row["password_hash"], candidate):
            return row
    except Exception:
        return None
    return None


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
        if not session.get("ok"):
            return jsonify(error="unauthorized"), 401
        # Player-USER sessions (Phase 5) carry a player_id and a per-user password
        # stamp — the two global stamps below can't cover them. Validate against the
        # live row instead: a missing/disabled user or a reset password (stamp
        # mismatch) is rejected immediately, so delete/disable/reset all log the user
        # out. The fresh row is stashed on g for the run-scope helper (§3).
        if session.get("role") == "player" and session.get("player_id") is not None:
            from .state import state
            row = state().db.get_player(session["player_id"])
            if not row or not row.get("enabled"):
                return jsonify(error="unauthorized"), 401
            if session.get("pw") != password_stamp(row["password_hash"], ""):
                return jsonify(error="unauthorized"), 401
            g.player = row
            return f(*args, **kwargs)
        # Admin, or the shared guest (RUN_PASSWORD) player with no player_id: a
        # session is valid if its stamp matches the admin or the run-only password.
        valid = {current_app.config.get("PW_STAMP"),
                 current_app.config.get("RUN_PW_STAMP")}
        valid.discard(None)
        if session.get("pw") not in valid:
            return jsonify(error="unauthorized"), 401
        return f(*args, **kwargs)
    return wrapper


def is_player() -> bool:
    """True when the current session authenticated with the run-only password."""
    return session.get("role") == "player"
