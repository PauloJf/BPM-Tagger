"""JSON auth endpoints for the React SPA (login / logout / me).

These mirror the session, CSRF and per-IP lockout semantics of the Jinja
``auth`` blueprint but speak JSON so the SPA can drive them with
``fetch(..., {credentials: 'same-origin'})`` + an ``X-CSRF-Token`` header. The
legacy form routes (``/login`` / ``/logout``) stay intact for the M0 Jinja UI.
"""

import logging
import time

from flask import Blueprint, current_app, jsonify, request, session

from ...config import __version__, save_settings
from ..auth import (_check_csrf, _csrf_token, password_stamp, verify_player,
                    verify_run_password, verify_ui_password)
from ..state import state

log = logging.getLogger(__name__)

api_auth_bp = Blueprint("api_auth", __name__)


@api_auth_bp.route("/api/login", methods=["POST"])
def api_login():
    """Authenticate via JSON. Reuses the same lockout counters as the form login."""
    st = state()
    data = request.get_json(force=True, silent=True) or {}
    password = data.get("password", "")
    username = str(data.get("username", "") or "").strip()
    ip = request.remote_addr or "unknown"
    # The account this attempt targets: a named player by username, or the shared
    # key for a blank-username attempt (admin or the RUN_PASSWORD guest). Keyed
    # lowercase to match the case-insensitive username matching in verify_player,
    # so case variants can't dodge the per-account counter.
    account_key = username.lower() if username else "__shared__"
    now = time.time()
    with st.login_lock:
        if st.login_locked(ip, account_key, now):
            return jsonify(ok=False, error="locked_out"), 429
        # Resolution order (Phase 5):
        #  * username given → a named player user (Run page only).
        #  * username blank → admin password wins, else the shared-guest run
        #    password grants the restricted "player" role (unchanged legacy flow).
        role = None
        player_id = None
        # Set when the admin password is right but the TOTP code was wrong/absent,
        # so the failure below can be reported as a code problem (not a password
        # one) — the password's validity is already implied by that point anyway.
        bad_code = False
        if isinstance(password, str) and password:
            if username:
                player = verify_player(username, password)
                if player:
                    role = "player"
                    player_id = player["id"]
                    stamp = password_stamp(player["password_hash"], "")
            elif verify_ui_password(password):
                # Admin password correct. Enforce the second factor if enabled.
                if st.config.get("totp_enabled"):
                    code = str(data.get("totp", "") or "").strip()
                    if not code:
                        # First step done — ask for the code and resubmit. This is
                        # not a failed attempt, so it doesn't feed the lockout.
                        return jsonify(ok=False, error="totp_required"), 401
                    if _consume_second_factor(st, code):
                        role = "admin"
                        stamp = current_app.config.get("PW_STAMP")
                    else:
                        bad_code = True
                else:
                    role = "admin"
                    stamp = current_app.config.get("PW_STAMP")
            elif verify_run_password(password):
                role = "player"
                stamp = current_app.config.get("RUN_PW_STAMP")
        if role:
            st.login_succeeded(ip, account_key)
            session["ok"] = True
            session["role"] = role
            session["pw"] = stamp
            session["player_id"] = player_id   # None for admin + shared guest
            if player_id is not None:
                st.db.touch_player_login(player_id)
            # Persistent (sliding) session for both roles so the configured
            # lifetime is actually honored: admins get UI_SESSION_HOURS (via
            # PERMANENT_SESSION_LIFETIME), players get the longer RUN_SESSION
            # window (see _RoleSessionInterface).
            session.permanent = True
            return jsonify(ok=True, role=role, csrf_token=_csrf_token())
        # A wrong code is a real failed attempt — count it so code guessing is
        # rate-limited by the same lockout as password guessing.
        st.login_failed(ip, account_key, now)
        if bad_code:
            return jsonify(ok=False, error="totp_invalid"), 401
    return jsonify(ok=False, error="invalid_password"), 401


def _consume_second_factor(st, code: str) -> bool:
    """True if ``code`` is the current admin TOTP, or an unused recovery code —
    which it then consumes (removed from the stored hashes and persisted). Called
    while holding ``login_lock``."""
    from ..totp import normalize_recovery_code
    from ..totp import verify as totp_verify
    if totp_verify(st.config.get("totp_secret", ""), code):
        return True
    from werkzeug.security import check_password_hash
    hashes = list(st.config.get("totp_recovery_hashes") or [])
    candidate = normalize_recovery_code(code)
    for h in hashes:
        try:
            matched = check_password_hash(h, candidate)
        except Exception:
            matched = False
        if matched:
            hashes.remove(h)
            st.config["totp_recovery_hashes"] = hashes
            save_settings(st.settings_path, {"totp_recovery_hashes": hashes})
            return True
    return False


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
    role = session.get("role") if authenticated else None
    is_admin = role == "admin"
    resp = {
        "authenticated": authenticated,
        "role": role,
        "version": __version__,
        "csrf_token": _csrf_token(),
    }
    # Player identity (Phase 5): a named player user reports its username; it is always
    # playlist-scoped (full_access=False), so the Run source picker hides Whole-library/
    # Starred. The shared Guest login (no player_id) and admin are full-access with no
    # username. (The server still gates every request regardless of what the client shows.)
    if role == "player":
        pid = session.get("player_id")
        if pid is not None and st.db is not None:
            prow = st.db.get_player(pid)
            resp["username"] = prow["username"] if prow else None
            resp["full_access"] = False      # named player users are always scoped
        else:
            resp["username"] = None          # shared Guest login (RUN_PASSWORD)
            resp["full_access"] = True
    elif is_admin:
        resp["username"] = None
        resp["full_access"] = True
    # Library stats and the install-ping prompt are admin-only — a player session
    # is a locked-down kiosk and never sees them.
    if is_admin and st.db is not None:
        try:
            resp["review_count"] = st.db.get_stats().get("needs_review", 0)
        except Exception:
            resp["review_count"] = 0
    else:
        resp["review_count"] = 0
    # Prompt for the one-time install ping only for an admin, when a ping URL is
    # configured and the user hasn't answered yet. See install_ping.py.
    resp["install_ping_ask"] = bool(
        is_admin
        and st.config.get("install_ping_consent") is None
        and str(st.config.get("install_ping_url") or "").strip()
    )
    return jsonify(resp)
