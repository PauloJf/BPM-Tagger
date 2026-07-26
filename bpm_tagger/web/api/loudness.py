"""Loudness endpoints: a bulk back-fill job + a single-track re-measure.

New scans measure loudness inline (scanner → `analyze_loudness`), so this exists
for libraries that were already scanned before the feature landed: the back-fill
walks every track with no `loudness_lufs` yet and measures it.

Mirrors the bulk lyrics fill's background-job shape (module-level progress dict
behind a lock, start/cancel/status). Unlike that one this work is CPU-bound
rather than network-bound, so it deliberately runs on a single thread — it is
back-fill, and starving an in-flight scan of workers would be a bad trade.
"""

import logging
import threading
from pathlib import Path

from flask import Blueprint, jsonify, request

from ...bpm.loudness import analyze_loudness
from ..auth import _check_csrf, login_required
from ..state import _assert_in_music_dir, state

log = logging.getLogger(__name__)

loudness_bp = Blueprint("api_loudness", __name__)

# How many tracks one job pass will look at. Matches the lyrics fill's cap so a
# huge library can't build an unbounded work list in memory.
_FILL_LIMIT = 5000

_fill = {"running": False, "total": 0, "done": 0,
         "measured": 0, "tagged": 0, "failed": 0, "cancel": False}
_fill_lock = threading.Lock()


def _run_loudness_fill(st):
    try:
        paths = st.db.get_unmeasured_loudness_paths(limit=_FILL_LIMIT)
        with _fill_lock:
            _fill.update(running=True, total=len(paths), done=0,
                         measured=0, tagged=0, failed=0, cancel=False)
        for path in paths:
            with _fill_lock:
                if _fill["cancel"]:
                    break
            try:
                lufs, source = analyze_loudness(path)
                if lufs is None:
                    # Unreadable / too short / silent. Left NULL, so it plays at
                    # full volume and a later pass will retry it.
                    with _fill_lock:
                        _fill["failed"] += 1
                else:
                    st.db.save_loudness(path, lufs, source)
                    with _fill_lock:
                        _fill["tagged" if source == "tag" else "measured"] += 1
            except Exception as exc:
                log.warning("Loudness back-fill failed for %s: %s", Path(path).name, exc)
                with _fill_lock:
                    _fill["failed"] += 1
            with _fill_lock:
                _fill["done"] += 1
    except Exception as exc:
        log.error("Loudness back-fill job failed: %s", exc)
    finally:
        with _fill_lock:
            _fill["running"] = False


@loudness_bp.route("/api/loudness/fill/start", methods=["POST"])
@login_required
def api_loudness_fill_start():
    _check_csrf()
    st = state()
    with _fill_lock:
        if _fill["running"]:
            return jsonify(ok=False, error="already_running"), 409
        _fill.update(running=True, total=0, done=0,
                     measured=0, tagged=0, failed=0, cancel=False)
    threading.Thread(target=_run_loudness_fill, args=(st,),
                     name="loudness-fill", daemon=True).start()
    return jsonify(ok=True)


@loudness_bp.route("/api/loudness/fill/cancel", methods=["POST"])
@login_required
def api_loudness_fill_cancel():
    _check_csrf()
    with _fill_lock:
        _fill["cancel"] = True
    return jsonify(ok=True)


@loudness_bp.route("/api/loudness/fill/status")
@login_required
def api_loudness_fill_status():
    st = state()
    remaining = st.db.count_unmeasured_loudness() if st.db else 0
    with _fill_lock:
        return jsonify(running=_fill["running"], total=_fill["total"], done=_fill["done"],
                       measured=_fill["measured"], tagged=_fill["tagged"],
                       failed=_fill["failed"], remaining=remaining)


@loudness_bp.route("/api/track/loudness/measure", methods=["POST"])
@login_required
def api_track_loudness_measure():
    """Force a fresh measurement for one track, ignoring any ReplayGain tag.

    The per-track escape hatch for a value that looks wrong — e.g. a file whose
    RG1-era tag read a few LU off the real gated loudness.
    """
    _check_csrf()
    st = state()
    path = (request.get_json(force=True, silent=True) or {}).get("file_path", "")
    if not path:
        return jsonify(ok=False, error="file_path required"), 400
    _assert_in_music_dir(path)
    if not st.db.get_track(path):
        return jsonify(ok=False, error="not found"), 404
    lufs, source = analyze_loudness(path, prefer_tag=False)
    if lufs is None:
        return jsonify(ok=False, error="Could not measure this file's loudness."), 500
    st.db.save_loudness(path, lufs, source)
    log.info("UI: measured loudness for %s → %.2f LUFS", Path(path).name, lufs)
    return jsonify(ok=True, loudness_lufs=lufs, loudness_source=source)
