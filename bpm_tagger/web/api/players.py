"""Player-user administration (Phase 5, docs/plans/phase5-player-users.md §4).

Admin-only CRUD for the local player accounts that log into Run mode. Every player
user is scoped to a set of playlists via player_playlists — a full-library non-admin
login is the shared Guest login (RUN_PASSWORD) only. Passwords are werkzeug hashes,
exactly like the admin and RUN_PASSWORD credentials.

These routes are deliberately NOT in _PLAYER_ALLOWED, so the app-factory player-scope
gate already 403s any player session before it reaches here; the in-handler
``session.role == "player"`` check is belt-and-suspenders, mirroring the guard in
``api_settings_run_password``.
"""

import logging
import sqlite3

from flask import Blueprint, jsonify, request, session

from ..auth import _check_csrf, login_required, verify_ui_password
from ..state import state
from .listen import _LISTEN_MODES

log = logging.getLogger(__name__)

players_bp = Blueprint("api_players", __name__)

MIN_PASSWORD = 8


def _admin_only():
    """None if the caller is an admin, else a (json, status) 403 tuple."""
    if session.get("role") == "player":
        return jsonify(error="forbidden"), 403
    return None


def _hash(pw: str):
    from werkzeug.security import generate_password_hash
    return generate_password_hash(pw)


def _validate_password(pw: str):
    """None if OK, else an (json, status) error tuple."""
    if len(pw) < MIN_PASSWORD:
        return jsonify(error=f"Password must be at least {MIN_PASSWORD} characters."), 400
    if verify_ui_password(pw):
        return jsonify(error="Password must differ from the admin password."), 400
    return None


def _clean_listen_mode(raw):
    """(value, error) for a per-user Listen-mode override.

    None / "" / "inherit" all mean "no override — follow the global
    player_listen_mode setting"; anything else must be one of the four modes."""
    if raw is None:
        return None, None
    mode = str(raw).strip().lower()
    if mode in ("", "inherit"):
        return None, None
    if mode not in _LISTEN_MODES:
        return None, (jsonify(error="listen_mode must be off, on, default, only or null."), 400)
    return mode, None


def _clean_ids(raw) -> list:
    if not isinstance(raw, list):
        return []
    out = []
    for x in raw:
        try:
            out.append(int(x))
        except (ValueError, TypeError):
            continue
    return out


@players_bp.route("/api/players", methods=["GET"])
@login_required
def list_players():
    guard = _admin_only()
    if guard:
        return guard
    users = state().db.list_players()
    # Never leak the hash to the client.
    for u in users:
        u.pop("password_hash", None)
    return jsonify(players=users)


@players_bp.route("/api/players", methods=["POST"])
@login_required
def create_player():
    guard = _admin_only()
    if guard:
        return guard
    _check_csrf()
    data = request.get_json(force=True, silent=True) or {}
    username = str(data.get("username") or "").strip()
    if not username:
        return jsonify(error="A username is required."), 400
    password = str(data.get("password") or "")
    bad = _validate_password(password)
    if bad:
        return bad
    mode, bad = _clean_listen_mode(data.get("listen_mode"))
    if bad:
        return bad
    db = state().db
    try:
        # Player users are always playlist-scoped; a full-library non-admin login
        # is the shared Guest login (RUN_PASSWORD) only.
        pid = db.add_player(username, _hash(password), False,
                            _clean_ids(data.get("playlist_ids")), mode)
    except sqlite3.IntegrityError:
        return jsonify(error="That username is already taken."), 409
    user = db.get_player(pid)
    user["playlist_ids"] = sorted(db.playlist_ids_for_player(pid))
    user.pop("password_hash", None)
    return jsonify(ok=True, player=user)


@players_bp.route("/api/players/<int:pid>", methods=["PATCH"])
@login_required
def patch_player(pid):
    guard = _admin_only()
    if guard:
        return guard
    _check_csrf()
    db = state().db
    if not db.get_player(pid):
        return jsonify(error="not_found"), 404
    data = request.get_json(force=True, silent=True) or {}
    kwargs = {}
    # `full_access` is intentionally ignored: player users are always playlist-scoped.
    if "enabled" in data:
        kwargs["enabled"] = bool(data["enabled"])
    if "playlist_ids" in data:
        kwargs["playlist_ids"] = _clean_ids(data["playlist_ids"])
    if "listen_mode" in data:
        # Present-but-null clears the override (back to inheriting the global
        # setting); absent leaves it untouched (see update_player's sentinel).
        mode, bad = _clean_listen_mode(data["listen_mode"])
        if bad:
            return bad
        kwargs["listen_mode"] = mode
    db.update_player(pid, **kwargs)
    user = db.get_player(pid)
    user["playlist_ids"] = sorted(db.playlist_ids_for_player(pid))
    user.pop("password_hash", None)
    return jsonify(ok=True, player=user)


@players_bp.route("/api/players/<int:pid>/password", methods=["POST"])
@login_required
def reset_player_password(pid):
    guard = _admin_only()
    if guard:
        return guard
    _check_csrf()
    db = state().db
    if not db.get_player(pid):
        return jsonify(error="not_found"), 404
    new_pw = str((request.get_json(force=True, silent=True) or {}).get("new_password") or "")
    bad = _validate_password(new_pw)
    if bad:
        return bad
    # Changing the hash changes the session stamp, so the user's existing sessions
    # stop validating (see login_required) — a reset logs them out everywhere.
    db.set_player_password(pid, _hash(new_pw))
    return jsonify(ok=True)


@players_bp.route("/api/players/<int:pid>", methods=["DELETE"])
@login_required
def delete_player(pid):
    guard = _admin_only()
    if guard:
        return guard
    _check_csrf()
    # delete_player also drops the player_playlists join rows (no SQLite FKs).
    state().db.delete_player(pid)
    return jsonify(ok=True)
