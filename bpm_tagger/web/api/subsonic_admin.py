"""Admin controls for the optional Subsonic API: the toggle and the admin
account's Subsonic credentials (docs/plans/subsonic-api.md).

Always registered, even while the API itself is off, so credentials can be
prepared before the restart that turns ``/rest`` on. Admin-only: the player
role is refused by the default-deny scope in ``web/app.py``, and again here.
"""

import secrets

from flask import Blueprint, current_app, jsonify, request, session

from ...config import save_settings
from ..auth import _check_csrf, login_required
from ..state import state

subsonic_admin_bp = Blueprint("api_subsonic_admin", __name__)

OWNER = "admin"  # Phase 1: the admin account only


def _forbidden():
    if session.get("role") == "player":
        return jsonify(error="forbidden"), 403
    return None


def _status():
    from ..subsonic.auth import subsonic_username
    st = state()
    cred = st.db.get_subsonic_credentials(OWNER) or {}
    return {
        "enabled": bool(st.config.get("subsonic_enabled")),
        # Whether /rest is actually being served by THIS process (the toggle
        # takes effect on restart).
        "active": "subsonic" in current_app.blueprints,
        "allow_plain_password": bool(st.config.get("subsonic_allow_plain_password")),
        "username": subsonic_username(),
        "has_api_key": bool(cred.get("api_key_hash")),
        "has_password": bool(cred.get("password")),
        "last_used_at": cred.get("last_used_at"),
    }


@subsonic_admin_bp.route("/api/subsonic")
@login_required
def api_subsonic_status():
    return _forbidden() or jsonify(_status())


@subsonic_admin_bp.route("/api/subsonic/settings", methods=["POST"])
@login_required
def api_subsonic_settings():
    guard = _forbidden()
    if guard:
        return guard
    _check_csrf()
    st = state()
    data = request.get_json(force=True, silent=True) or {}
    updates = {}
    for key in ("subsonic_enabled", "subsonic_allow_plain_password"):
        if key in data:
            updates[key] = bool(data[key])
    st.config.update(updates)
    if updates:
        save_settings(st.settings_path, updates)
    status = _status()
    return jsonify(ok=True, restart_required=status["enabled"] != status["active"], **status)


@subsonic_admin_bp.route("/api/subsonic/api-key", methods=["POST", "DELETE"])
@login_required
def api_subsonic_api_key():
    """POST generates a new API key (replacing any previous one) and returns it
    ONCE — only its hash is stored. DELETE revokes it."""
    guard = _forbidden()
    if guard:
        return guard
    _check_csrf()
    from ..subsonic.auth import hash_api_key
    db = state().db
    if request.method == "DELETE":
        db.set_subsonic_api_key_hash(OWNER, None)
        return jsonify(ok=True)
    key = secrets.token_urlsafe(32)
    db.set_subsonic_api_key_hash(OWNER, hash_api_key(key))
    return jsonify(ok=True, api_key=key)


@subsonic_admin_bp.route("/api/subsonic/password", methods=["POST", "DELETE"])
@login_required
def api_subsonic_password():
    """POST generates a new random Subsonic password (for clients without API
    key support) and returns it once. DELETE revokes it."""
    guard = _forbidden()
    if guard:
        return guard
    _check_csrf()
    db = state().db
    if request.method == "DELETE":
        db.set_subsonic_password(OWNER, None)
        return jsonify(ok=True)
    # URL-safe and unambiguous enough to type into a phone: 24 chars ≈ 143 bits.
    password = secrets.token_urlsafe(18)
    db.set_subsonic_password(OWNER, password)
    return jsonify(ok=True, password=password)
