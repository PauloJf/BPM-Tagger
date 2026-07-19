"""Run mode: build a tempo-locked playback queue for a target cadence.

Given a target BPM, select tracks whose detected BPM — octave-folded to ×½/×1/×2
when enabled, so a 75 BPM song serves a 150 SPM run at native speed — lands
within a configurable tolerance of the target. Starred tracks are preferred.
Each returned track carries the playback rate the client applies via
``audio.playbackRate`` (pitch preserved by the browser).

POSTing an ``exclude`` list of file paths — the client's auto-refill sends the
tracks already in the queue — drops those from consideration so an ongoing run
doesn't re-queue what just played. If every eligible match is excluded (a
small library, a long run), the exclusion is dropped and the full pool is
reshuffled instead of starving the refill; ``recycled: true`` marks that.
"""

import os
import random

from flask import Blueprint, g, jsonify, request

from ..auth import _check_csrf, login_required
from ..state import state

run_bp = Blueprint("api_run", __name__)

MAX_EXCLUDE = 500  # defensive cap — the client sends a bounded recent-history window
# Force-tempo playbackRate clamp: octave folding keeps most tracks well inside this,
# so the clamp only bites genuine outliers (e.g. a 60 BPM track forced to a 180 target).
RATE_MIN, RATE_MAX = 0.5, 2.0


def _run_scope():
    """(full_access, allowed_playlist_ids) for the current session (Phase 5).

    ``g.player`` is set by login_required only for named player-user sessions;
    admin and the shared Guest login leave it unset → full access. Every named
    player user is scoped to the playlists it's associated with — the
    library/starred pool and other playlists are off-limits. (The legacy
    ``full_access`` column is no longer honored: a full-library non-admin login
    is the shared Guest login only.)"""
    p = getattr(g, "player", None)
    if p is None:
        return True, None
    return False, state().db.playlist_ids_for_player(p["id"])


def _fold(bpm: float, target: float, octave: bool) -> float:
    """The BPM candidate (×½ / ×1 / ×2 when octave folding) closest to target."""
    cands = (bpm, bpm / 2, bpm * 2) if octave else (bpm,)
    return min(cands, key=lambda c: abs(target - c))


@run_bp.route("/api/run/queue", methods=["GET", "POST"])
@login_required
def api_run_queue():
    st = state()
    cfg = st.config
    if request.method == "POST":
        _check_csrf()
        body = request.get_json(silent=True) or {}
        bpm_raw = body.get("bpm")
        count_raw = body.get("count")
        playlist_raw = body.get("playlist")
        force_raw = body.get("force")
        exclude = body.get("exclude") or []
        if not isinstance(exclude, list):
            return jsonify(error="exclude must be a list of paths"), 400
        exclude_set = {str(p) for p in exclude[:MAX_EXCLUDE]}
    else:
        bpm_raw = request.args.get("bpm")
        count_raw = request.args.get("count")
        playlist_raw = request.args.get("playlist")
        force_raw = request.args.get("force")
        exclude_set = set()

    # Optional playlist scope: restrict the candidate pool to that playlist's
    # matched local tracks instead of the whole library.
    playlist_id = None
    if playlist_raw not in (None, "", "library"):
        try:
            playlist_id = int(playlist_raw)
        except (ValueError, TypeError):
            return jsonify(error="playlist must be a playlist id"), 400
        if not st.db.get_playlist(playlist_id):
            return jsonify(error="playlist not found"), 404

    # Per-user scope (Phase 5): a restricted player may only run its own playlists,
    # never another playlist and never the whole-library / starred pool (no playlist).
    full, allowed = _run_scope()
    if not full:
        if playlist_id is None:
            return jsonify(error="forbidden"), 403
        if playlist_id not in allowed:
            return jsonify(error="forbidden"), 403

    try:
        target = float(bpm_raw)
    except (ValueError, TypeError):
        return jsonify(error="bpm (target) is required"), 400
    if not 30 <= target <= 300:
        return jsonify(error="bpm out of range (30-300)"), 400
    try:
        count = max(1, min(200, int(count_raw if count_raw is not None
                                     else cfg.get("run_queue_size", 20))))
    except (ValueError, TypeError):
        count = int(cfg.get("run_queue_size", 20))
    octave = bool(cfg.get("run_octave_fold", True))
    prefer_starred = bool(cfg.get("run_prefer_starred", True))
    prefer_familiar = bool(cfg.get("run_prefer_familiar", False))
    tol = max(0.005, float(cfg.get("run_tolerance_pct", 4.0)) / 100.0)
    # "Play everything, force tempo": drop the tolerance filter so every candidate
    # qualifies, forcing each to the target via playbackRate. Per-request flag wins;
    # the run_force_tempo setting is the default when the request doesn't say.
    if force_raw is None:
        force = bool(cfg.get("run_force_tempo", False))
    else:
        force = (force_raw if isinstance(force_raw, bool)
                 else str(force_raw).lower() in ("1", "true", "yes", "on"))

    # Score candidates: eligibility by post-fold deviation from target (minus
    # whatever's excluded), selection by (starred first, then most-played first
    # when familiarity is preferred, closest first).
    def _matches(cands, exclude_paths):
        found = []
        for t in cands:
            if t["file_path"] in exclude_paths:
                continue
            folded = _fold(t["bpm"], target, octave)
            dev = abs(target / folded - 1.0)
            if force or dev <= tol:   # force: every candidate qualifies
                found.append((t, folded, dev))
        found.sort(key=lambda x: (not x[0]["starred"] if prefer_starred else False,
                                  -(x[0]["play_count"] or 0) if prefer_familiar else 0,
                                  x[2]))
        return found

    def _library_pool(exclude_paths):
        """The wider pool a run tops up from (and the whole pool for a library-source
        run). A full-access session draws from the whole library; a scoped player
        draws only from its own assigned playlists — never the whole library, so a
        thin playlist can't leak out-of-scope tracks into the queue."""
        if full:
            return _matches(st.db.get_run_candidates(None), exclude_paths)
        return _matches(st.db.get_run_candidates_for_playlists(allowed), exclude_paths)

    def _build(exclude_paths):
        """Playlist matches first, then topped up from the session's wider pool at
        the same cadence when the playlist can't fill the queue — so a small
        playlist (few tracks matching this target) never degenerates into one
        song looping. A library-source run (no playlist) just matches that wider
        pool. Returns (matches, playlist_paths, topped_up) — the second item is
        the set of file paths that came from the playlist itself."""
        if playlist_id is None:
            return _library_pool(exclude_paths), set(), False
        pl = _matches(st.db.get_run_candidates(playlist_id), exclude_paths)
        pl_paths = {m[0]["file_path"] for m in pl}
        if len(pl) >= count:
            return pl, pl_paths, False
        # Not enough playlist tracks match here — fill the rest from the wider
        # pool, excluding what's already picked so nothing repeats.
        lib = _library_pool(set(exclude_paths) | pl_paths)
        return pl + lib, pl_paths, bool(lib)

    picked, pl_paths, topped_up = _build(exclude_set)
    recycled = False
    if not picked and exclude_set:
        # Everything eligible was excluded (small pool, long run) — drop the
        # exclusion and reshuffle rather than starve the refill.
        picked, pl_paths, topped_up = _build(set())
        recycled = True

    # Playback order shuffled (within the scored/truncated slice) so two runs
    # at the same target don't sound identical.
    picked = picked[:count]
    random.shuffle(picked)

    def _rate(folded):
        """playbackRate to hit the target, clamped in force mode so an outlier can't
        produce a chipmunk/rumble artifact. Returns (rate, clamped)."""
        raw = target / folded
        if force:
            r = min(RATE_MAX, max(RATE_MIN, raw))
            return round(r, 4), (r != raw)
        return round(raw, 4), False

    tracks = []
    for (t, folded, _dev) in picked:
        rate, clamped = _rate(folded)
        tracks.append({
            "path":    t["file_path"],
            "title":   t["title"] or os.path.splitext(os.path.basename(t["file_path"]))[0],
            "artist":  t["artist"] or "",
            "bpm":     t["bpm"],
            "starred": bool(t["starred"]),
            "play_count": t["play_count"],
            "run_bpm": round(folded, 2),              # BPM after octave fold
            "rate":    rate,                          # playbackRate to hit target
            # When clamped, playback is NOT exactly at target (the track was too far
            # off even after octave folding) — the UI notes it as "forced".
            "clamped": clamped,
            # From the selected playlist itself vs. a library top-up (marks the UI).
            "from_playlist": t["file_path"] in pl_paths,
        })
    return jsonify(tracks=tracks, target=target, count=len(tracks),
                   octave_fold=octave, tolerance_pct=tol * 100,
                   prefer_starred=prefer_starred, prefer_familiar=prefer_familiar,
                   recycled=recycled, topped_up=topped_up, playlist=playlist_id,
                   forced=force)


@run_bp.route("/api/run/stat", methods=["POST"])
@login_required
def api_run_stat():
    """Accumulate run-mode usage counters reported by the player.

    The client batches deltas since its last flush (roughly every 20s while a
    tempo-locked run plays, and on pause / track change / page hide) and posts
    them here; the server just adds them to the cumulative totals shown on the
    Stats page. Fire-and-forget from the client's view — always returns ok."""
    _check_csrf()
    body = request.get_json(silent=True) or {}
    deltas = body.get("deltas")
    if not isinstance(deltas, dict):
        return jsonify(error="deltas must be an object"), 400
    state().db.add_run_stats(deltas)
    return jsonify(ok=True)


@run_bp.route("/api/run/playlists")
@login_required
def api_run_playlists():
    """Playlists usable as a run source, with how many of each are actually
    runnable (matched local file + detected BPM). Filtered to the session user's
    playlists — all of them for admin / guest / full-access users, only the
    associated ones for a restricted player (Phase 5). Management stays on the
    admin Playlists page."""
    db = state().db
    full, _allowed = _run_scope()
    playlists = db.list_playlists() if full else db.list_playlists_for_player(g.player["id"])
    out = [{
        "id": p["id"],
        "name": p["name"],
        "source": p["source"],
        "image_url": p.get("image_url"),
        "available": db.count_run_candidates(p["id"]),
        "total": p.get("track_count") or p.get("indexed_count") or 0,
    } for p in playlists]
    return jsonify(playlists=out)
