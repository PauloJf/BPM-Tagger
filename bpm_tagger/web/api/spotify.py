"""Spotify connection endpoints (§3): status, authorize-url, callback, disconnect.

The OAuth callback is exempt from login_required/CSRF (Spotify redirects the
browser here) and is validated by matching the ``state`` param against the value
stashed in the session when the authorize URL was minted.
"""

import logging
import secrets

from flask import Blueprint, jsonify, redirect, request, session

from ..auth import _check_csrf, login_required
from ..state import state

log = logging.getLogger(__name__)

spotify_bp = Blueprint("api_spotify", __name__)


def _grabber():
    st = state()
    return getattr(st.tagger, "grabber", None) if st.tagger else None


def _redirect_target(path: str) -> str:
    base = (state().config.get("ui_public_url") or "").rstrip("/")
    return f"{base}{path}" if base else path


@spotify_bp.route("/api/spotify/status")
@login_required
def spotify_status():
    g = _grabber()
    if not g:
        return jsonify(enabled=False, configured=False, connected=False)
    s = g.status()
    s["enabled"] = True
    return jsonify(s)


@spotify_bp.route("/api/spotify/authorize-url")
@login_required
def spotify_authorize_url():
    g = _grabber()
    if not g:
        return jsonify(error="grabber_disabled"), 409
    if not g.client.is_configured():
        return jsonify(error="not_configured"), 400
    st_token = secrets.token_urlsafe(24)
    session["spotify_oauth_state"] = st_token
    return jsonify(url=g.authorize_url(st_token))


@spotify_bp.route("/api/spotify/callback")
def spotify_callback():
    """Browser lands here after Spotify consent. State-validated; not login/CSRF gated."""
    g = _grabber()
    if not g:
        return redirect(_redirect_target("/settings?spotify=disabled"))

    err = request.args.get("error")
    if err:
        return redirect(_redirect_target(f"/settings?spotify={err}"))

    state_param = request.args.get("state", "")
    expected = session.pop("spotify_oauth_state", None)
    if not expected or state_param != expected:
        return redirect(_redirect_target("/settings?spotify=state_mismatch"))

    code = request.args.get("code", "")
    if not code:
        return redirect(_redirect_target("/settings?spotify=no_code"))
    try:
        g.handle_callback(code)
    except Exception as exc:
        log.error("Spotify callback failed: %s", exc)
        return redirect(_redirect_target("/settings?spotify=error"))
    return redirect(_redirect_target("/settings?spotify=connected"))


@spotify_bp.route("/api/spotify/disconnect", methods=["POST"])
@login_required
def spotify_disconnect():
    _check_csrf()
    g = _grabber()
    if not g:
        return jsonify(error="grabber_disabled"), 409
    g.client.disconnect()
    return jsonify(ok=True)
