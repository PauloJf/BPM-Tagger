"""Listen mode: a regular (non-cadence) playback queue built from a playlist.

The Run queue endpoint is deliberately unusable as a plain player — it requires
a target BPM and only returns tracks that can reach it. This endpoint is the
non-cadence counterpart: every playable track of one playlist (or the pooled
"mine" source) in playlist order, BPM or not, so the Listen page can play a
playlist top to bottom and its radio mode can keep drawing from the same pool.

Availability for the kiosk (player role) is governed by the admin's
``player_listen_mode`` setting (off | on | default | only). The endpoint sits in
the app factory's default-deny ``_PLAYER_ALLOWED`` list, and additionally 403s
player sessions itself while the mode is ``off`` — so turning the feature off
actually turns it off, not just hides the tab. Admin and guest-with-full-access
sessions are never gated (the page is always routable for them).
"""

import os

from flask import Blueprint, jsonify, request, session

from ..auth import login_required
from ..state import state
from .run import _run_scope

listen_bp = Blueprint("api_listen", __name__)

_LISTEN_MODES = ("off", "on", "default", "only")


def listen_mode(cfg) -> str:
    """The configured kiosk listen mode, normalized to a known value."""
    mode = str(cfg.get("player_listen_mode", "off") or "off").strip().lower()
    return mode if mode in _LISTEN_MODES else "off"


@listen_bp.route("/api/listen/queue")
@login_required
def api_listen_queue():
    """The playable tracks of ?playlist= (an id, or "mine" for every playlist the
    session may play, unioned), in playlist order. "Playable" = the row matched a
    live library file — a detected BPM is NOT required, unlike the run queue.

    The client owns ordering beyond this (its shuffle toggle) and the radio
    refill (it re-fetches this list and appends what it hasn't played recently),
    so this stays a plain listing rather than a sampler."""
    st = state()
    if session.get("role") == "player" and listen_mode(st.config) == "off":
        return jsonify(error="forbidden"), 403

    full, allowed = _run_scope()
    raw = request.args.get("playlist")
    pooled = str(raw).lower() == "mine"
    playlist_id = None
    if not pooled:
        try:
            playlist_id = int(raw)
        except (ValueError, TypeError):
            return jsonify(error="playlist must be a playlist id or \"mine\""), 400
        if not st.db.get_playlist(playlist_id):
            return jsonify(error="playlist not found"), 404
        if not full and playlist_id not in allowed:
            return jsonify(error="forbidden"), 403

    if pooled:
        ids = sorted(allowed) if not full else [p["id"] for p in st.db.list_playlists()]
    else:
        ids = [playlist_id]

    tracks, seen = [], set()
    for pid in ids:
        for r in st.db.get_playlist_tracks(pid):
            path = r.get("local_file_path")
            # Only rows whose join hit a live library file are playable; the
            # pooled source dedupes on that path (a track on two playlists
            # plays once).
            if r["derived_status"] != "have" or not path or path in seen:
                continue
            seen.add(path)
            tracks.append({
                "path":    path,
                "title":   r["title"] or os.path.splitext(os.path.basename(path))[0],
                "artist":  r.get("local_artist") or r["artist"] or "",
                "bpm":     r.get("local_bpm"),
                "starred": bool(r.get("local_starred")),
                "disliked": bool(r.get("local_disliked")),
                "duration_ms": r.get("local_duration_ms") or r.get("duration_ms"),
                "loudness_lufs": r.get("local_loudness_lufs"),
            })
    return jsonify(tracks=tracks, playlist=("mine" if pooled else playlist_id),
                   count=len(tracks))
