"""Cross-device player state: the SPA's queue snapshot, stored per account.

GET returns the stored snapshot (opaque JSON authored by the SPA) with the
server's write stamp; PUT replaces it (``state: null`` clears it). The owner is
derived from the session — the admin (all admin sessions share one snapshot) or
a named player user. The shared Guest login (RUN_PASSWORD, no account row) has
nowhere to store it, so both verbs report ``sync: false`` and the SPA stays on
per-browser localStorage — mirroring how /api/accent treats the Guest.

Conflict model is deliberately simple (snapshot, last writer wins): the SPA
adopts the server copy on boot/foreground when the stamp is one it hasn't seen,
and otherwise pushes its own state. No merging, no per-field diffs.
"""

import json
import logging

from flask import Blueprint, jsonify, request

from ..auth import _check_csrf, login_required, session_owner
from ..state import state

log = logging.getLogger(__name__)

player_state_bp = Blueprint("api_player_state", __name__)

# Hard cap on a stored snapshot. A long queue (hundreds of tracks with paths,
# titles, artists) is tens of KB; anything near this is a client bug, not a
# bigger queue.
_MAX_STATE_BYTES = 512 * 1024


def _owner():
    """The account key this session's state lives under, or None for the shared
    Guest login (role player, no player_id), which has no account row.

    Same owner-key convention as attribution (``auth.session_owner``); the Guest
    bucket that attribution keeps has nowhere to store a queue snapshot, so it
    maps to None here."""
    owner = session_owner()
    return None if owner == "guest" else owner


@player_state_bp.route("/api/player/state", methods=["GET"])
@login_required
def api_player_state_get():
    owner = _owner()
    if owner is None:
        return jsonify(sync=False, state=None, updated_at=None)
    row = state().db.get_player_state(owner)
    if not row:
        return jsonify(sync=True, state=None, updated_at=None)
    try:
        snapshot = json.loads(row["state"])
    except Exception:
        snapshot = None
    return jsonify(sync=True, state=snapshot, updated_at=row["updated_at"])


@player_state_bp.route("/api/player/state", methods=["PUT"])
@login_required
def api_player_state_put():
    _check_csrf()
    owner = _owner()
    if owner is None:
        # Guest: accepted as a no-op so the SPA needn't special-case it.
        return jsonify(ok=True, sync=False, updated_at=None)
    data = request.get_json(force=True, silent=True) or {}
    snapshot = data.get("state", None)
    if snapshot is None:
        state().db.clear_player_state(owner)
        return jsonify(ok=True, sync=True, updated_at=None)
    # The snapshot is opaque, but it must at least be the SPA's shape — a dict
    # with a queue list — so a malformed client can't store junk that then
    # crashes every other device on adoption.
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("queue"), list):
        return jsonify(ok=False, error="invalid_state"), 400
    raw = json.dumps(snapshot, separators=(",", ":"))
    if len(raw.encode("utf-8")) > _MAX_STATE_BYTES:
        return jsonify(ok=False, error="state_too_large"), 413
    stamp = state().db.save_player_state(owner, raw)
    return jsonify(ok=True, sync=True, updated_at=stamp)
