"""Waveform back-fill job: start/cancel/status.

A scan only stores waveform peaks when something will show them
(``COMPUTE_WAVEFORMS``; ``auto`` = UI on — see ``config.waveforms_enabled``), so a
library scanned headless has none. ``/api/waveform`` already recomputes a single
track on demand; this job fills every missing one up front, for people who
want them all ready before they press play.

Same shape as the loudness back-fill (``web/api/loudness.py``): module-level
progress dict behind a lock, single-threaded on purpose, since this is CPU-bound
back-fill and must not starve an in-flight scan of workers.
"""

import logging
import threading
from pathlib import Path

from flask import Blueprint, jsonify

from ...bpm.waveform import compute_waveform_peaks
from ..auth import _check_csrf, login_required
from ..state import state

log = logging.getLogger(__name__)

waveform_bp = Blueprint("api_waveform", __name__)

# Per-pass cap, matching the loudness and lyrics fills.
_FILL_LIMIT = 5000

_fill = {"running": False, "total": 0, "done": 0, "filled": 0, "failed": 0, "cancel": False}
_fill_lock = threading.Lock()


def _run_waveform_fill(st):
    try:
        paths = st.db.get_missing_waveform_paths(limit=_FILL_LIMIT)
        with _fill_lock:
            _fill.update(running=True, total=len(paths), done=0, filled=0, failed=0, cancel=False)
        for path in paths:
            with _fill_lock:
                if _fill["cancel"]:
                    break
            try:
                raw = compute_waveform_peaks(path)
                if raw is None:
                    # Unreadable — left NULL; the on-demand endpoint or a later pass retries.
                    with _fill_lock:
                        _fill["failed"] += 1
                else:
                    st.db.save_waveform_peaks(path, raw)
                    with _fill_lock:
                        _fill["filled"] += 1
            except Exception as exc:
                log.warning("Waveform back-fill failed for %s: %s", Path(path).name, exc)
                with _fill_lock:
                    _fill["failed"] += 1
            with _fill_lock:
                _fill["done"] += 1
    except Exception as exc:
        log.error("Waveform back-fill job failed: %s", exc)
    finally:
        with _fill_lock:
            _fill["running"] = False


@waveform_bp.route("/api/waveform/fill/start", methods=["POST"])
@login_required
def api_waveform_fill_start():
    _check_csrf()
    st = state()
    with _fill_lock:
        if _fill["running"]:
            return jsonify(ok=False, error="already_running"), 409
        _fill.update(running=True, total=0, done=0, filled=0, failed=0, cancel=False)
    threading.Thread(target=_run_waveform_fill, args=(st,),
                     name="waveform-fill", daemon=True).start()
    return jsonify(ok=True)


@waveform_bp.route("/api/waveform/fill/cancel", methods=["POST"])
@login_required
def api_waveform_fill_cancel():
    _check_csrf()
    with _fill_lock:
        _fill["cancel"] = True
    return jsonify(ok=True)


@waveform_bp.route("/api/waveform/fill/status")
@login_required
def api_waveform_fill_status():
    st = state()
    remaining = st.db.count_missing_waveforms() if st.db else 0
    with _fill_lock:
        return jsonify(running=_fill["running"], total=_fill["total"], done=_fill["done"],
                       filled=_fill["filled"], failed=_fill["failed"], remaining=remaining)
