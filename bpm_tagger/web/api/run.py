"""Run mode: build a tempo-locked playback queue for a target cadence.

Given a target BPM, select tracks whose detected BPM — octave-folded to ×½/×1/×2
when enabled, so a 75 BPM song serves a 150 SPM run at native speed — can be
pulled onto the target within the max-stretch limit (``run_stretch_limit_pct``).
That one limit is the whole eligibility rule: a track that can't reach the
cadence within it never enters the queue. Starred tracks are preferred. Each
returned track carries the playback rate the client applies via
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

from ...config import RUN_PRESET_DEFAULTS
from ..auth import _check_csrf, login_required, session_owner
from ..state import state

run_bp = Blueprint("api_run", __name__)

MAX_EXCLUDE = 500  # defensive cap — the client sends a bounded recent-history window
# Slack on the eligibility comparison so a track sitting exactly on the limit isn't
# dropped by float rounding (150/120 is exact, 120/100 is not). Matches the same
# epsilon in the frontend's QueueSimilar reachability check.
_LIMIT_EPS = 1e-9


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


def _eligible(cands, target: float, octave: bool, limit: float) -> list:
    """The candidates that can reach `target`, as (track, folded_bpm, deviation).

    This is the one eligibility rule the whole of Run mode turns on, shared by
    the queue builder and the cadence-readiness views so "what can I run at 165"
    can never drift from what the queue would actually pick. Unordered — callers
    apply their own preference sort."""
    found = []
    for t in cands:
        folded = _fold(t["bpm"], target, octave)
        dev = abs(target / folded - 1.0)
        if dev <= limit + _LIMIT_EPS:
            found.append((t, folded, dev))
    return found


def preset_counts(cands, presets: list[dict], octave: bool, limit: float) -> dict:
    """How many of `cands` are runnable at each configured preset.

    The one place the cadence rule is folded against the presets, so the playlist
    cards' quiet badges (via /api/run/readiness) and the playlist detail page's
    stats strip can never disagree about "11 tracks at 155". Preset BPMs are JSON
    object keys, so they're strings."""
    return {str(p["bpm"]): len(_eligible(cands, float(p["bpm"]), octave, limit))
            for p in presets}


def _run_settings(cfg) -> tuple[bool, float]:
    """(octave_fold, stretch limit as a fraction) — the two knobs eligibility
    depends on, read the same way everywhere."""
    return (bool(cfg.get("run_octave_fold", True)),
            max(0.01, float(cfg.get("run_stretch_limit_pct", 15.0)) / 100.0))


def _presets(cfg) -> list[dict]:
    """The configured run presets as {name, bpm}, normalizing the legacy
    bare-number entries the way the Run page does."""
    out = []
    raw = cfg.get("run_presets") or []
    if not isinstance(raw, list):
        raw = []
    for i, p in enumerate(raw[:4]):
        dflt_name, dflt_bpm = RUN_PRESET_DEFAULTS[min(i, 3)]
        if isinstance(p, dict):
            try:
                bpm = int(float(p.get("bpm", dflt_bpm)))
            except (ValueError, TypeError):
                bpm = dflt_bpm
            out.append({"name": str(p.get("name") or dflt_name), "bpm": bpm})
        elif isinstance(p, (int, float)) and not isinstance(p, bool):
            out.append({"name": dflt_name, "bpm": int(p)})
    if not out:
        out = [{"name": n, "bpm": b} for n, b in RUN_PRESET_DEFAULTS]
    return out


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
        exclude = body.get("exclude") or []
        if not isinstance(exclude, list):
            return jsonify(error="exclude must be a list of paths"), 400
        exclude_set = {str(p) for p in exclude[:MAX_EXCLUDE]}
    else:
        bpm_raw = request.args.get("bpm")
        count_raw = request.args.get("count")
        playlist_raw = request.args.get("playlist")
        exclude_set = set()
    # A stale client (a tab that outlived the deploy) may still send ?force=1 —
    # it's simply never read, so the request succeeds against the new rule.

    # Optional playlist scope: restrict the candidate pool to that playlist's
    # matched local tracks instead of the whole library. The "mine" sentinel is the
    # pooled source — every playlist the session may run, unioned (Phase 5); it flows
    # through the playlist_id-None branch of _build, which for a scoped player draws
    # from its own playlists (never the whole library).
    playlist_id = None
    pooled = str(playlist_raw).lower() == "mine"
    if not pooled and playlist_raw not in (None, "", "library"):
        try:
            playlist_id = int(playlist_raw)
        except (ValueError, TypeError):
            return jsonify(error="playlist must be a playlist id"), 400
        if not st.db.get_playlist(playlist_id):
            return jsonify(error="playlist not found"), 404

    # Per-user scope (Phase 5): a restricted player may only run its own playlists —
    # one at a time, or all of them pooled via "mine" — never another playlist and
    # never the whole-library / starred pool (no playlist).
    full, allowed = _run_scope()
    if not full and not pooled:
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
    prefer_starred = bool(cfg.get("run_prefer_starred", True))
    prefer_familiar = bool(cfg.get("run_prefer_familiar", False))
    # Max stretch is the single eligibility rule: how far playbackRate may move
    # from 1 to land a track on the target. Enforced here at selection so nothing
    # unreachable enters the queue, and again client-side by lockRate at playback
    # (the target slider can move a built queue, and the limit itself can drop).
    octave, limit = _run_settings(cfg)

    # Eligibility is the shared _eligible rule; this adds the queue-only parts —
    # dropping what the caller excluded, then the preference sort (starred first,
    # then most-played first when familiarity is preferred, closest first).
    def _matches(cands, exclude_paths):
        found = _eligible(
            [t for t in cands if t["file_path"] not in exclude_paths],
            target, octave, limit)
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

    tracks = []
    for (t, folded, _dev) in picked:
        tracks.append({
            "path":    t["file_path"],
            "title":   t["title"] or os.path.splitext(os.path.basename(t["file_path"]))[0],
            "artist":  t["artist"] or "",
            "bpm":     t["bpm"],
            "starred": bool(t["starred"]),
            "play_count": t["play_count"],
            # Integrated loudness (LUFS) so the player can level this track; NULL
            # for anything not measured yet, which plays at full volume.
            "loudness_lufs": t["loudness_lufs"],
            "run_bpm": round(folded, 2),              # BPM after octave fold
            # playbackRate to hit target — in-limit by construction (see _matches).
            "rate":    round(target / folded, 4),
            # From the selected playlist itself vs. a library top-up (marks the UI).
            "from_playlist": t["file_path"] in pl_paths,
        })
    return jsonify(tracks=tracks, target=target, count=len(tracks),
                   octave_fold=octave, stretch_limit_pct=limit * 100,
                   prefer_starred=prefer_starred, prefer_familiar=prefer_familiar,
                   recycled=recycled, topped_up=topped_up, playlist=playlist_id)


def _target_arg():
    """Parse and range-check ?bpm=, matching the queue endpoint's rules.
    Returns (target, None) or (None, error_response)."""
    try:
        target = float(request.args.get("bpm"))
    except (ValueError, TypeError):
        return None, (jsonify(error="bpm (target) is required"), 400)
    if not 30 <= target <= 300:
        return None, (jsonify(error="bpm out of range (30-300)"), 400)
    return target, None


@run_bp.route("/api/run/ready")
@login_required
def api_run_ready():
    """Every library track that could be run at ?bpm=, closest cadence first.

    The cadence-view counterpart to /api/run/queue: same octave fold, same
    stretch limit, same derived fields — but the whole eligible set rather than a
    shuffled, count-capped, preference-sorted slice, so the page can answer
    "what can I actually run at 165?" and hand the lot to the player or a
    playlist. Whole-library only; scoping a run to one playlist is what the Run
    page's source picker is for.

    Deliberately absent from the player allowlist in web/app.py, which is
    default-deny — players get the Run page, not the library-wide view."""
    st = state()
    target, err = _target_arg()
    if err:
        return err
    octave, limit = _run_settings(st.config)

    found = _eligible(st.db.get_run_candidates(None), target, octave, limit)
    found.sort(key=lambda x: x[2])          # closest to the target first
    tracks = [{
        "path":    t["file_path"],
        "title":   t["title"] or os.path.splitext(os.path.basename(t["file_path"]))[0],
        "artist":  t["artist"] or "",
        "bpm":     t["bpm"],
        "starred": bool(t["starred"]),
        "play_count": t["play_count"],
        "loudness_lufs": t["loudness_lufs"],
        "run_bpm": round(folded, 2),
        "rate":    round(target / folded, 4),
    } for (t, folded, _dev) in found]
    return jsonify(tracks=tracks, target=target, count=len(tracks),
                   octave_fold=octave, stretch_limit_pct=limit * 100)


@run_bp.route("/api/run/readiness")
@login_required
def api_run_readiness():
    """How many tracks are runnable at each configured preset, for the library
    and for every playlist — the numbers behind the cadence cards and the
    per-playlist badges.

    One candidate pass per scope, folded against each preset in Python by the
    shared preset_counts() helper: the pool is already in memory and the fold is a
    handful of floats per track, whereas expressing octave folding in SQL would
    mean four queries and a rule that could drift from _eligible.

    Admin/guest only via the default-deny player allowlist, like /api/run/ready."""
    st = state()
    octave, limit = _run_settings(st.config)
    presets = _presets(st.config)

    def counts(cands) -> dict:
        return preset_counts(cands, presets, octave, limit)

    library = counts(st.db.get_run_candidates(None))
    playlists = [{"id": p["id"], "name": p["name"],
                  "counts": counts(st.db.get_run_candidates(p["id"]))}
                 for p in st.db.list_playlists()]
    return jsonify(presets=presets, stretch_limit_pct=limit * 100,
                   octave_fold=octave, library=library, playlists=playlists)


@run_bp.route("/api/run/stat", methods=["POST"])
@login_required
def api_run_stat():
    """Accumulate run-mode usage counters reported by the player.

    The client batches deltas since its last flush (roughly every 20s while a
    tempo-locked run plays, and on pause / track change / page hide) and posts
    them here; the server adds them to the cumulative totals shown on the Stats
    page. Fire-and-forget from the client's view — always returns ok.

    The same batch also carries the run's context (``run: {source, target}``),
    which attributes it to this session's account and to a server-derived run
    row — the journal. No client-generated run id: the server decides which run
    an event belongs to (same owner, same source, within the idle window), so a
    reloaded tab or a second device can't fork one run into two. ``end: true``
    (the client's queue was replaced by a non-run queue, the tempo lock was
    released, or it signed out) closes that run at its last event; a run whose
    close never arrives is closed lazily by the idle window instead."""
    _check_csrf()
    body = request.get_json(silent=True) or {}
    deltas = body.get("deltas")
    if not isinstance(deltas, dict):
        return jsonify(error="deltas must be an object"), 400
    owner = session_owner()
    ctx = body.get("run")
    run_id = state().db.add_run_stats(
        deltas, owner=owner, run=ctx if isinstance(ctx, dict) else None)
    if body.get("end"):
        state().db.close_run(owner)
        run_id = None
    return jsonify(ok=True, run_id=run_id)


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
