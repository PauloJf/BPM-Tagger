"""Playlist endpoints (§3): list/add/patch/delete, tracks (have|missing|queued),
manual sync. Also the consolidated grabber status poll."""

import logging
import re

from flask import Blueprint, Response, jsonify, request

from ...config import __version__
from ...grabber.spotify import SpotifyError, parse_playlist_id
from ..auth import _check_csrf, login_required
from ..state import state


def _versions() -> dict:
    v = {"app": __version__, "yt_dlp": None}
    try:
        import yt_dlp
        v["yt_dlp"] = getattr(getattr(yt_dlp, "version", None), "__version__", None)
    except Exception:
        pass
    return v

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
        last_change=db.get_last_change(),
        versions=_versions(),
    )


@playlists_bp.route("/api/playlists", methods=["GET"])
@login_required
def list_playlists():
    return jsonify(playlists=state().db.list_playlists())


@playlists_bp.route("/api/playlists", methods=["POST"])
@login_required
def add_playlist():
    _check_csrf()
    data = request.get_json(force=True, silent=True) or {}
    source = str(data.get("source") or "spotify").lower()
    if source == "navidrome":
        return _add_navidrome_playlist(data)
    return _add_spotify_playlist(data)


def _add_spotify_playlist(data):
    g = _grabber()
    if not g:
        return jsonify(error="grabber_disabled"), 409
    if not g.client.is_connected():
        return jsonify(error="not_connected"), 400
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


def _add_navidrome_playlist(data):
    from ...integrations.navidrome_playlists import navidrome_configured, sync_navidrome_playlist
    cfg = state().config
    if not navidrome_configured(cfg):
        return jsonify(error="navidrome_not_configured"), 400
    nid = str(data.get("navidrome_id") or data.get("id") or "").strip()
    if not nid:
        return jsonify(error="A Navidrome playlist id is required."), 400
    db = state().db
    row_id = db.add_navidrome_playlist(nid, str(data.get("name") or ""))
    try:
        sync_navidrome_playlist(db, cfg, row_id)
    except Exception as exc:
        log.warning("Navidrome playlist add/sync failed: %s", exc)
        return jsonify(error=str(exc)), 400
    return jsonify(ok=True, playlist=db.get_playlist(row_id))


@playlists_bp.route("/api/navidrome/playlists")
@login_required
def navidrome_my_playlists():
    """Importable Navidrome playlists (the configured user's own + public),
    flagged with watched state. Independent of the grabber."""
    from ...integrations.navidrome_playlists import list_navidrome_playlists, navidrome_configured
    cfg = state().config
    if not navidrome_configured(cfg):
        return jsonify(error="navidrome_not_configured"), 400
    try:
        playlists = list_navidrome_playlists(cfg)
    except Exception as exc:
        log.warning("Navidrome playlist listing failed: %s", exc)
        return jsonify(error=str(exc)), 400
    watched = {p.get("navidrome_id") for p in state().db.list_playlists()
               if p.get("source") == "navidrome"}
    for p in playlists:
        p["watched"] = p["navidrome_id"] in watched
    return jsonify(playlists=playlists)


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
    if status not in ("", "have", "missing", "queued", "removed"):
        status = ""
    tracks = db.get_playlist_tracks(pid, status)
    if not status:
        # Full detail view acknowledges the "new" badges.
        db.mark_playlist_seen(pid)
    return jsonify(playlist=pl, tracks=tracks)


@playlists_bp.route("/api/playlists/<int:pid>/export.m3u")
@login_required
def export_m3u(pid):
    db = state().db
    pl = db.get_playlist(pid)
    if not pl:
        return jsonify(error="not_found"), 404
    lines = ["#EXTM3U"]
    for r in db.get_playlist_track_rows(pid):
        if r.get("matched_file_path"):
            dur = int((r.get("duration_ms") or 0) / 1000)
            lines.append(f"#EXTINF:{dur},{r.get('artist', '')} - {r.get('title', '')}")
            lines.append(r["matched_file_path"])
    body = "\n".join(lines) + "\n"
    fname = re.sub(r'[^\w.-]+', "_", pl.get("name") or "playlist") or "playlist"
    return Response(body, mimetype="audio/x-mpegurl",
                    headers={"Content-Disposition": f'attachment; filename="{fname}.m3u"'})


@playlists_bp.route("/api/playlists/<int:pid>/sync", methods=["POST"])
@login_required
def sync_playlist(pid):
    _check_csrf()
    db = state().db
    pl = db.get_playlist(pid)
    if not pl:
        return jsonify(error="not_found"), 404
    if pl.get("source") == "navidrome":
        from ...integrations.navidrome_playlists import navidrome_configured, sync_navidrome_playlist
        cfg = state().config
        if not navidrome_configured(cfg):
            return jsonify(error="navidrome_not_configured"), 400
        try:
            pl = sync_navidrome_playlist(db, cfg, pid)
        except Exception as exc:
            log.warning("Navidrome playlist sync failed: %s", exc)
            return jsonify(error=str(exc)), 400
        return jsonify(ok=True, playlist=pl)
    # Spotify (default): needs the grabber + a live connection.
    g = _grabber()
    if not g:
        return jsonify(error="grabber_disabled"), 409
    if not g.client.is_connected():
        return jsonify(error="not_connected"), 400
    try:
        pl = g.sync.sync_playlist(pid)
    except SpotifyError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(ok=True, playlist=pl)
