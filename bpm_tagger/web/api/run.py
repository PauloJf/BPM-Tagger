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

from flask import Blueprint, jsonify, request

from ..auth import login_required
from ..state import state

run_bp = Blueprint("api_run", __name__)

MAX_EXCLUDE = 500  # defensive cap — the client sends a bounded recent-history window


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
        body = request.get_json(silent=True) or {}
        bpm_raw = body.get("bpm")
        count_raw = body.get("count")
        exclude = body.get("exclude") or []
        if not isinstance(exclude, list):
            return jsonify(error="exclude must be a list of paths"), 400
        exclude_set = {str(p) for p in exclude[:MAX_EXCLUDE]}
    else:
        bpm_raw = request.args.get("bpm")
        count_raw = request.args.get("count")
        exclude_set = set()

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
    tol = max(0.005, float(cfg.get("run_tolerance_pct", 4.0)) / 100.0)

    # Score every candidate: eligibility by post-fold deviation from target
    # (minus whatever's excluded), selection by (starred first, closest first).
    def _matches(exclude_paths):
        found = []
        for t in st.db.get_run_candidates():
            if t["file_path"] in exclude_paths:
                continue
            folded = _fold(t["bpm"], target, octave)
            dev = abs(target / folded - 1.0)
            if dev <= tol:
                found.append((t, folded, dev))
        found.sort(key=lambda x: (not x[0]["starred"] if prefer_starred else False, x[2]))
        return found

    picked = _matches(exclude_set)
    recycled = False
    if not picked and exclude_set:
        picked = _matches(set())
        recycled = True

    # Playback order shuffled (within the scored/truncated slice) so two runs
    # at the same target don't sound identical.
    picked = picked[:count]
    random.shuffle(picked)

    tracks = [{
        "path":    t["file_path"],
        "title":   t["title"] or os.path.splitext(os.path.basename(t["file_path"]))[0],
        "artist":  t["artist"] or "",
        "bpm":     t["bpm"],
        "starred": bool(t["starred"]),
        "run_bpm": round(folded, 2),                  # BPM after octave fold
        "rate":    round(target / folded, 4),         # playbackRate to hit target
    } for (t, folded, _dev) in picked]
    return jsonify(tracks=tracks, target=target, count=len(tracks),
                   octave_fold=octave, tolerance_pct=tol * 100,
                   prefer_starred=prefer_starred, recycled=recycled)
