"""Ecosystem link: this app as the identity provider for companion apps.

Off unless ``ECOSYSTEM_SECRET`` (env only, 32+ characters) is set; every route
then answers 404, as if it did not exist. With it, a signed-in admin or member
can fetch a 60-second, single-use signed token that a companion app served on
the same origin exchanges for its own session. Kiosk players and the shared
guest never get one. The secret itself never leaves the server.

Contract: ``docs/contracts/identity-token-v1.json``.
Decision: ``docs/plans/ecosystem/ADR-002-identity.md``.
"""

import logging

from flask import Blueprint, jsonify, request, session

from .. import ecosystem_token
from ..auth import admin_username, login_required
from ..state import state

log = logging.getLogger("bpm_tagger")

ecosystem_bp = Blueprint("api_ecosystem", __name__)

ISSUER = "bpm-tagger"
AUDIENCES = ("music-grabber",)


def secret() -> str:
    """The shared secret, or "" when the link is off (unset or too short)."""
    value = state().config.get("ecosystem_secret") or ""
    return value if ecosystem_token.usable_secret(value) else ""


def _identity():
    """``(sub, username, role)`` for this session, or None when it may not leave this app."""
    role = session.get("role")
    if role == "admin":
        return "admin", admin_username() or "admin", "admin"
    if role == "member" and session.get("player_id") is not None:
        row = state().db.get_player(session["player_id"])
        if row and row.get("enabled") and row.get("role") == "member":
            return f"player:{row['id']}", row["username"], "member"
    return None


@ecosystem_bp.route("/api/ecosystem/token")
@login_required
def ecosystem_token_route():
    key = secret()
    if not key:
        return jsonify(error="not_found"), 404
    aud = request.args.get("aud", "")
    if aud not in AUDIENCES:
        return jsonify(error="unknown_audience", message="unknown audience"), 400
    who = _identity()
    if who is None:
        return jsonify(error="forbidden", message="this account can't sign in to other apps"), 403
    sub, username, role = who
    token = ecosystem_token.mint(key, iss=ISSUER, aud=aud, sub=sub, username=username, role=role)
    log.info("Ecosystem: sign-on token issued to %s for %s", sub, aud)
    resp = jsonify(token=token, expires_in=ecosystem_token.DEFAULT_TTL)
    resp.headers["Cache-Control"] = "no-store"
    return resp
