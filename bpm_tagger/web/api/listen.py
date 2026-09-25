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

Rating-weighted picking (docs/plans/ratings-weighted-picking.md): the queue
overlays the caller's own rating/dislike/star (``annotate_marks``);
``?order=weighted`` returns the same tracks in a rating-weighted permutation
with the caller's dislikes dropped (D12/D13 — in-order play keeps them, shown
with the dislike mark); ``POST /api/listen/pick`` draws a weighted batch for
the radio's refill. ``_source_tracks`` is the one place that resolves a
``?playlist=`` value into a pool, shared by all three."""

import os
import random

from typing import Optional

from flask import Blueprint, g, jsonify, request, session

from ..auth import _check_csrf, login_required, session_owner
from ..state import state
from ..weighting import sample, weighted_order, weights_from_config
from .run import _run_scope

listen_bp = Blueprint("api_listen", __name__)

MAX_EXCLUDE = 500  # same defensive cap as the Run queue's exclude list

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


def _source_tracks(st, raw):
    """Resolve ``?playlist=`` (an id, "mine" or "library") to (label, tracks) —
    tracks carry ``file_path``/``title``/``artist``/``bpm``/``play_count`` (the
    fields ``annotate_marks`` and the weighted sampler need) plus
    ``duration_ms``/``loudness_lufs``. Returns (None, (response, status)) for a
    scope violation or an unknown playlist, so every caller (the queue, order=
    weighted, and pick) enforces the same rules from one place."""
    full, allowed = _run_scope()
    kind = str(raw).lower()

    if kind == "library":
        # Whole-library source: full-access sessions only, mirroring the Run
        # source rule — a scoped player never reaches past its playlists.
        if not full:
            return None, (jsonify(error="forbidden"), 403)
        tracks = [{
            "file_path": t["file_path"],
            "title":     t["title"] or os.path.splitext(os.path.basename(t["file_path"]))[0],
            "artist":    t["artist"] or "",
            "bpm":       t["bpm"],
            "play_count": t.get("play_count"),
            "duration_ms": t["duration_ms"],
            "loudness_lufs": t["loudness_lufs"],
        } for t in st.db.get_listen_library()]
        return ("library", tracks), None

    pooled = kind == "mine"
    playlist_id = None
    if not pooled:
        try:
            playlist_id = int(raw)
        except (ValueError, TypeError):
            return None, (jsonify(
                error="playlist must be a playlist id, \"mine\" or \"library\""), 400)
        if not st.db.get_playlist(playlist_id):
            return None, (jsonify(error="playlist not found"), 404)
        if not full and playlist_id not in allowed:
            return None, (jsonify(error="forbidden"), 403)

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
                "file_path": path,
                "title":     r["title"] or os.path.splitext(os.path.basename(path))[0],
                "artist":    r.get("local_artist") or r["artist"] or "",
                "bpm":       r.get("local_bpm"),
                "play_count": r.get("local_play_count"),
                "duration_ms": r.get("local_duration_ms") or r.get("duration_ms"),
                "loudness_lufs": r.get("local_loudness_lufs"),
            })
    return ("mine" if pooled else playlist_id, tracks), None


def _serialize(t: dict) -> dict:
    """A track dict (post annotate_marks) into the wire shape the Listen
    endpoints share."""
    return {
        "path":    t["file_path"],
        "title":   t["title"],
        "artist":  t["artist"],
        "bpm":     t["bpm"],
        "starred": bool(t.get("starred")),
        "rating":  t.get("rating"),
        "disliked": bool(t.get("disliked")),
        "duration_ms": t.get("duration_ms"),
        "loudness_lufs": t.get("loudness_lufs"),
    }


@listen_bp.route("/api/listen/queue")
@login_required
def api_listen_queue():
    """The playable tracks of ?playlist= — an id, "mine" (every playlist the
    session may play, unioned), or "library" (the whole library, full-access
    sessions only) — in playlist/shelf order by default, or ``?order=weighted``
    for a rating-weighted permutation with the caller's dislikes dropped
    (D12/D13). "Playable" = a live library file; a detected BPM is NOT
    required, unlike the run queue.

    In-order play always includes disliked tracks (shown with the mark) — only
    the weighted order drops them. The client owns further ordering (its
    shuffle toggle for album/artist, which stays uniform per D12) and the radio
    refill (POST /api/listen/pick)."""
    st = state()
    if session.get("role") == "player" and effective_listen_mode(st) == "off":
        return jsonify(error="forbidden"), 403

    resolved, err = _source_tracks(st, request.args.get("playlist"))
    if err:
        return err
    label, raw_tracks = resolved
    owner = session_owner()
    rows = st.db.annotate_marks(raw_tracks, owner)

    if str(request.args.get("order", "")).lower() == "weighted":
        weights = weights_from_config(st.config, owner)
        rows = [r for r in weighted_order(rows, weights.weight, random.Random())
                if not r.get("disliked")]

    tracks = [_serialize(r) for r in rows]
    return jsonify(tracks=tracks, playlist=label, count=len(tracks))


@listen_bp.route("/api/listen/pick", methods=["POST"])
@login_required
def api_listen_pick():
    """A rating-weighted batch drawn from ?playlist='s pool for the radio's
    refill — the POST counterpart of ``?order=weighted`` that also honours an
    ``exclude`` list (the tracks already in the client's queue), the same
    recycle-when-exhausted rule as the Run queue: if nothing is left once the
    caller's dislikes and the excluded paths are dropped, the exclusion is
    dropped and the full (non-disliked) pool is resampled instead of starving
    the refill."""
    st = state()
    if session.get("role") == "player" and effective_listen_mode(st) == "off":
        return jsonify(error="forbidden"), 403
    _check_csrf()
    body = request.get_json(silent=True) or {}

    resolved, err = _source_tracks(st, body.get("playlist"))
    if err:
        return err
    label, raw_tracks = resolved
    exclude = body.get("exclude") or []
    if not isinstance(exclude, list):
        return jsonify(error="exclude must be a list of paths"), 400
    exclude_set = {str(p) for p in exclude[:MAX_EXCLUDE]}
    try:
        count = max(1, min(100, int(body.get("count", 20))))
    except (ValueError, TypeError):
        count = 20

    owner = session_owner()
    rows = st.db.annotate_marks(raw_tracks, owner)
    pool = [r for r in rows if not r.get("disliked")]
    weights = weights_from_config(st.config, owner)
    rng = random.Random()

    picked = sample([r for r in pool if r["file_path"] not in exclude_set],
                    count, weights.weight, rng)
    recycled = False
    if not picked and pool:
        picked = sample(pool, count, weights.weight, rng)
        recycled = True

    tracks = [_serialize(r) for r in picked]
    return jsonify(tracks=tracks, playlist=label, recycled=recycled)
