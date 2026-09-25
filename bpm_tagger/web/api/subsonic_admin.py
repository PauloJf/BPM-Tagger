"""Admin controls for the optional Subsonic API: the toggle and each account's
Subsonic credentials (docs/plans/subsonic-api.md).

Always registered, even while the API itself is off, so credentials can be
prepared before the restart that turns ``/rest`` on. Admin-only: the player
role is refused by the default-deny scope in ``web/app.py``, and again here.

Accounts are the admin (``admin``) and player users (``player:<id>``). Having
credentials *is* the per-account Subsonic access switch: a player has none until
the admin generates some, and revoking both turns its access off.
"""

import secrets

from flask import Blueprint, current_app, jsonify, request, session

from ...config import save_settings
from ..auth import _check_csrf, login_required
from ..state import state

subsonic_admin_bp = Blueprint("api_subsonic_admin", __name__)


def _forbidden():
    if session.get("role") == "player":
        return jsonify(error="forbidden"), 403
    return None


def _account(owner: str, username: str, kind: str, cred: dict, enabled: bool = True) -> dict:
    return {
        "owner": owner, "username": username, "kind": kind, "enabled": enabled,
        "has_api_key": bool(cred.get("api_key_hash")),
        "has_password": bool(cred.get("password")),
        "last_used_at": cred.get("last_used_at"),
    }


def _status():
    from ..subsonic.auth import ADMIN_OWNER, player_owner, subsonic_username
    from ..subsonic.transcode import ffmpeg_path
    st = state()
    creds = {c["owner"]: c for c in st.db.list_subsonic_credentials()}
    accounts = [_account(ADMIN_OWNER, subsonic_username(), "admin", creds.get(ADMIN_OWNER, {}))]
    for p in st.db.list_players():
        owner = player_owner(p["id"])
        accounts.append(_account(owner, p["username"], "player", creds.get(owner, {}),
                                 bool(p.get("enabled"))))
    return {
        "enabled": bool(st.config.get("subsonic_enabled")),
        # Whether /rest is actually being served by THIS process (the toggle
        # takes effect on restart).
        "active": "subsonic" in current_app.blueprints,
        "allow_plain_password": bool(st.config.get("subsonic_allow_plain_password")),
        "transcode": bool(st.config.get("subsonic_transcode")),
        "ffmpeg_available": ffmpeg_path() is not None,
        "run_playlists": bool(st.config.get("subsonic_run_playlists", True)),
        "fetch_lyrics": bool(st.config.get("subsonic_fetch_lyrics")),
        "accounts": accounts,
    }


def _valid_owner(owner: str) -> bool:
    if owner == "admin":
        return True
    if owner.startswith("player:") and owner[7:].isdigit():
        return state().db.get_player(int(owner[7:])) is not None
    return False


@subsonic_admin_bp.route("/api/subsonic")
@login_required
def api_subsonic_status():
    return _forbidden() or jsonify(_status())


@subsonic_admin_bp.route("/api/subsonic/clients")
@login_required
def api_subsonic_clients():
    """Subsonic apps seen in the last hour and what each is playing (live,
    in memory — see web/subsonic/activity.py). Empty while the API is off."""
    guard = _forbidden()
    if guard:
        return guard
    from ..subsonic.activity import registry as activity
    import time
    return jsonify(now=time.time(), clients=activity.snapshot())


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
    for key in ("subsonic_enabled", "subsonic_allow_plain_password",
                "subsonic_transcode", "subsonic_run_playlists", "subsonic_fetch_lyrics"):
        if key in data:
            updates[key] = bool(data[key])
    st.config.update(updates)
    if updates:
        save_settings(st.settings_path, updates)
    status = _status()
    return jsonify(ok=True, restart_required=status["enabled"] != status["active"], **status)


@subsonic_admin_bp.route("/api/subsonic/accounts/<owner>/api-key", methods=["POST", "DELETE"])
@login_required
def api_subsonic_api_key(owner):
    """POST generates a new API key (replacing any previous one) and returns it
    ONCE — only its hash is stored. DELETE revokes it."""
    guard = _forbidden()
    if guard:
        return guard
    _check_csrf()
    if not _valid_owner(owner):
        return jsonify(error="not_found"), 404
    from ..subsonic.auth import hash_api_key
    db = state().db
    if request.method == "DELETE":
        db.set_subsonic_api_key_hash(owner, None)
        return jsonify(ok=True)
    key = secrets.token_urlsafe(32)
    db.set_subsonic_api_key_hash(owner, hash_api_key(key))
    return jsonify(ok=True, api_key=key)


@subsonic_admin_bp.route("/api/subsonic/accounts/<owner>/password", methods=["POST", "DELETE"])
@login_required
def api_subsonic_password(owner):
    """POST generates a new random Subsonic password (for clients without API
    key support) and returns it once. DELETE revokes it."""
    guard = _forbidden()
    if guard:
        return guard
    _check_csrf()
    if not _valid_owner(owner):
        return jsonify(error="not_found"), 404
    db = state().db
    if request.method == "DELETE":
        db.set_subsonic_password(owner, None)
        return jsonify(ok=True)
    # URL-safe and short enough to type into a phone: 24 chars ≈ 143 bits.
    password = secrets.token_urlsafe(18)
    db.set_subsonic_password(owner, password)
    return jsonify(ok=True, password=password)
