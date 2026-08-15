"""Listen mode: a regular (non-cadence) playback queue built from a playlist.

The Run queue endpoint is deliberately unusable as a plain player — it requires
a target BPM and only returns tracks that can reach it. This endpoint is the
non-cadence counterpart: every playable track of one playlist (or the pooled
"mine" source) in playlist order, BPM or not, so the Listen page can play a
playlist top to bottom and its radio mode can keep drawing from the same pool.

Availability for the kiosk (player role) is governed by the admin's
``player_listen_mode`` setting (off | on | default | only), which a NAMED player
user may override on its own account (``players.listen_mode``; NULL = inherit).
``effective_listen_mode`` is the single resolver for that — every gate and
``/api/me`` read through it, so a per-user override can never be honored in one
place and ignored in another. The endpoint sits in the app factory's
default-deny ``_PLAYER_ALLOWED`` list, and additionally 403s player sessions
itself while the effective mode is ``off`` — so turning the feature off actually
turns it off, not just hides the tab. Admin and guest-with-full-access sessions
are never gated (the page is always routable for them).
"""

import os

from typing import Optional

from flask import Blueprint, g, jsonify, request, session

from ..auth import login_required
from ..state import state
from .run import _run_scope

listen_bp = Blueprint("api_listen", __name__)

_LISTEN_MODES = ("off", "on", "default", "only")


def listen_mode(cfg) -> str:
    """The configured GLOBAL kiosk listen mode, normalized to a known value."""
    mode = str(cfg.get("player_listen_mode", "off") or "off").strip().lower()
    return mode if mode in _LISTEN_MODES else "off"


def normalize_listen_mode(raw) -> Optional[str]:
    """A per-user override normalized to one of the four modes, or None when the
    value is unset/blank/unrecognised (= inherit the global setting)."""
    mode = str(raw or "").strip().lower()
    return mode if mode in _LISTEN_MODES else None


def effective_listen_mode(st=None, player: Optional[dict] = None) -> str:
    """The listen mode in force for the CURRENT session.

    A named player user's own ``listen_mode`` wins when set; anything else — a
    named user that inherits, the shared Guest login (RUN_PASSWORD, no account
    row) and the admin — follows the global ``player_listen_mode`` setting.
    (The admin's Listen page is always routable regardless; the value is only
    reported so the SPA can show what the kiosk would get.)

    ``player`` is the caller's already-loaded players row, if it has one; else
    ``g.player`` (set by login_required for named users) or a fresh lookup."""
    st = st or state()
    if session.get("role") == "player":
        pid = session.get("player_id")
        if pid is not None:
            row = player
            if row is None:
                row = getattr(g, "player", None)
            if row is None and st.db is not None:
                row = st.db.get_player(pid)
            override = normalize_listen_mode(row.get("listen_mode")) if row else None
            if override:
                return override
    return listen_mode(st.config)


@listen_bp.route("/api/listen/queue")
@login_required
def api_listen_queue():
    """The playable tracks of ?playlist= — an id, "mine" (every playlist the
    session may play, unioned), or "library" (the whole library, full-access
    sessions only) — in playlist/shelf order. "Playable" = a live library file;
    a detected BPM is NOT required, unlike the run queue.

    The client owns ordering beyond this (its shuffle toggle) and the radio
    refill (it re-fetches this list and appends what it hasn't played recently),
    so this stays a plain listing rather than a sampler."""
    st = state()
    if session.get("role") == "player" and effective_listen_mode(st) == "off":
        return jsonify(error="forbidden"), 403

    full, allowed = _run_scope()
    raw = request.args.get("playlist")
    kind = str(raw).lower()

    if kind == "library":
        # Whole-library source: full-access sessions only, mirroring the Run
        # source rule — a scoped player never reaches past its playlists.
        if not full:
            return jsonify(error="forbidden"), 403
        tracks = [{
            "path":    t["file_path"],
            "title":   t["title"] or os.path.splitext(os.path.basename(t["file_path"]))[0],
            "artist":  t["artist"] or "",
            "bpm":     t["bpm"],
            "starred": bool(t["starred"]),
            "disliked": bool(t["disliked"]),
            "duration_ms": t["duration_ms"],
            "loudness_lufs": t["loudness_lufs"],
        } for t in st.db.get_listen_library()]
        return jsonify(tracks=tracks, playlist="library", count=len(tracks))

    pooled = kind == "mine"
    playlist_id = None
    if not pooled:
        try:
            playlist_id = int(raw)
        except (ValueError, TypeError):
            return jsonify(error="playlist must be a playlist id, \"mine\" or \"library\""), 400
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
