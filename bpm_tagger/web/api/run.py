"""Run mode: build a tempo-locked playback queue for a target cadence.

Given a target BPM, select tracks whose detected BPM — octave-folded to ×½/×1/×2
when enabled, so a 75 BPM song serves a 150 SPM run at native speed — lands
within a configurable tolerance of the target. Starred tracks are preferred.
Each returned track carries the playback rate the client applies via
``audio.playbackRate`` (pitch preserved by the browser).
"""

import os
import random

from flask import Blueprint, jsonify, request

from ..auth import login_required
from ..state import state

run_bp = Blueprint("api_run", __name__)


def _fold(bpm: float, target: float, octave: bool) -> float:
    """The BPM candidate (×½ / ×1 / ×2 when octave folding) closest to target."""
    cands = (bpm, bpm / 2, bpm * 2) if octave else (bpm,)
    return min(cands, key=lambda c: abs(target - c))


@run_bp.route("/api/run/queue")
@login_required
def api_run_queue():
    st = state()
    cfg = st.config
    try:
        target = float(request.args.get("bpm", ""))
    except (ValueError, TypeError):
        return jsonify(error="bpm (target) is required"), 400
    if not 30 <= target <= 300:
        return jsonify(error="bpm out of range (30-300)"), 400
    try:
        count = max(1, min(200, int(request.args.get("count",
                                                     cfg.get("run_queue_size", 20)))))
    except (ValueError, TypeError):
        count = int(cfg.get("run_queue_size", 20))
    octave = bool(cfg.get("run_octave_fold", True))
    prefer_starred = bool(cfg.get("run_prefer_starred", True))
    tol = max(0.005, float(cfg.get("run_tolerance_pct", 4.0)) / 100.0)

    # Score every candidate: eligibility by post-fold deviation from target,
    # selection by (starred first, closest first), playback order shuffled so
    # two runs at the same target don't sound identical.
    picked = []
    for t in st.db.get_run_candidates():
        folded = _fold(t["bpm"], target, octave)
        dev = abs(target / folded - 1.0)
        if dev <= tol:
            picked.append((t, folded, dev))
    picked.sort(key=lambda x: (not x[0]["starred"] if prefer_starred else False, x[2]))
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
                   prefer_starred=prefer_starred)
