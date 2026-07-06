"""Playlist endpoints (§3): list/add/patch/delete, tracks (have|missing|queued),
manual sync. Also the consolidated grabber status poll."""

import logging

from flask import Blueprint, jsonify, request

from ...grabber.spotify import SpotifyError, parse_playlist_id
from ..auth import _check_csrf, login_required
from ..state import state

log = logging.getLogger(__name__)

playlists_bp = Blueprint("api_playlists", __name__)


def _grabber():
    st = state()
    return getattr(st.tagger, "grabber", None) if st.tagger else None


@playlists_bp.route("/api/grabber/status")
@login_required
def grabber_status():
    g = _grabber()
    if not g:
        return jsonify(enabled=False)
    db = state().db
    counts = db.get_queue_counts()
    return jsonify(
        enabled=True,
        spotify=g.status(),
        queue_counts=counts,
        active=db.get_active_grabs(),
        inbox_count=counts.get("awaiting_user", 0),
    )


@playlists_bp.route("/api/playlists", methods=["GET"])
@login_required
def list_playlists():
    return jsonify(playlists=state().db.list_playlists())


@playlists_bp.route("/api/playlists", methods=["POST"])
@login_required
def add_playlist():
    _check_csrf()
    g = _grabber()
    if not g:
        return jsonify(error="grabber_disabled"), 409
    if not g.client.is_connected():
        return jsonify(error="not_connected"), 400
    data = request.get_json(force=True, silent=True) or {}
    pid = parse_playlist_id(str(data.get("url") or data.get("id") or ""))
    if not pid:
        return jsonify(error="A Spotify playlist URL or ID is required."), 400
    try:
        meta = g.client.get_playlist_meta(pid)
    except SpotifyError as exc:
        return jsonify(error=str(exc)), 400
    row_id = state().db.add_playlist(meta["spotify_id"], meta["name"],
                                     meta["image_url"], meta["track_count"])
    g.request_sync()
    return jsonify(ok=True, playlist=state().db.get_playlist(row_id))


@playlists_bp.route("/api/playlists/<int:pid>", methods=["PATCH"])
@login_required
def patch_playlist(pid):
    _check_csrf()
    db = state().db
    if not db.get_playlist(pid):
        return jsonify(error="not_found"), 404
    data = request.get_json(force=True, silent=True) or {}
    if "enabled" in data:
        db.set_playlist_enabled(pid, bool(data["enabled"]))
    return jsonify(ok=True, playlist=db.get_playlist(pid))


@playlists_bp.route("/api/playlists/<int:pid>", methods=["DELETE"])
@login_required
def delete_playlist(pid):
    _check_csrf()
    state().db.delete_playlist(pid)
    return jsonify(ok=True)


@playlists_bp.route("/api/playlists/<int:pid>/tracks")
@login_required
def playlist_tracks(pid):
    db = state().db
    pl = db.get_playlist(pid)
    if not pl:
        return jsonify(error="not_found"), 404
    status = request.args.get("status", "")
    if status not in ("", "have", "missing", "queued"):
        status = ""
    return jsonify(playlist=pl, tracks=db.get_playlist_tracks(pid, status))


@playlists_bp.route("/api/playlists/<int:pid>/sync", methods=["POST"])
@login_required
def sync_playlist(pid):
    _check_csrf()
    g = _grabber()
    if not g:
        return jsonify(error="grabber_disabled"), 409
    if not g.client.is_connected():
        return jsonify(error="not_connected"), 400
    if not state().db.get_playlist(pid):
        return jsonify(error="not_found"), 404
    try:
        pl = g.sync.sync_playlist(pid)
    except SpotifyError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(ok=True, playlist=pl)
