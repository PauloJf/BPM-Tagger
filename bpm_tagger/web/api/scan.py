"""Scan control endpoints (start/pause/resume/stop/retry/reanalyze/refresh/restart)."""

import logging
import os
import sys
import threading
import time
from pathlib import Path

from flask import Blueprint, jsonify, request

from ..auth import _check_csrf, login_required
from ..state import _assert_in_music_dir, state

log = logging.getLogger(__name__)

scan_bp = Blueprint("api_scan", __name__)


@scan_bp.route("/api/scan/start", methods=["POST"])
@login_required
def api_scan_start():
    _check_csrf()
    st = state()
    if st.tagger is None:
        return jsonify(ok=False, error="tagger not available")
    if st.progress and st.progress.is_scanning:
        return jsonify(ok=False, error="scan already running")
    mode = st.config.get("mode", "watch")
    if mode == "scan_review":
        target = st.tagger.scan_review
    elif mode == "report":
        target = st.tagger.report
    elif mode in ("scan_all", "watch_all"):
        target = lambda: st.tagger.scan_directory(force=True)
    else:  # watch, scan_unscanned, default
        target = lambda: st.tagger.scan_directory(force=False)
    threading.Thread(target=target, daemon=True).start()
    return jsonify(ok=True)


@scan_bp.route("/api/scan/retry_errors", methods=["POST"])
@login_required
def api_scan_retry_errors():
    _check_csrf()
    st = state()
    if st.tagger is None:
        return jsonify(ok=False, error="tagger not available")
    if st.progress and st.progress.is_scanning:
        return jsonify(ok=False, error="scan already running")
    threading.Thread(target=st.tagger.retry_errors, daemon=True).start()
    return jsonify(ok=True)


@scan_bp.route("/api/scan/reanalyze", methods=["POST"])
@login_required
def api_reanalyze():
    _check_csrf()
    st = state()
    if st.tagger is None:
        return jsonify(ok=False, error="tagger not available")
    if st.progress and st.progress.is_scanning:
        return jsonify(ok=False, error="a scan is already running")
    data = request.get_json(force=True, silent=True) or {}
    file_path = data.get("file_path", "")
    if not file_path:
        return jsonify(ok=False, error="file_path required")
    _assert_in_music_dir(file_path)
    result = st.tagger.process_file(file_path, force=True)
    log.info("UI: re-analyzed %s → %s", Path(file_path).name, result.get("status"))
    return jsonify(ok=result.get("status") != "error", **result)


@scan_bp.route("/api/scan/refresh_hashes", methods=["POST"])
@login_required
def api_refresh_hashes():
    _check_csrf()
    st = state()
    if st.tagger is None:
        return jsonify(ok=False, error="tagger not available")
    if st.progress and st.progress.is_scanning:
        return jsonify(ok=False, error="cannot refresh hashes while a scan is running")
    updated, missing = st.db.refresh_hashes()
    log.info("Manual hash refresh: %d updated, %d not found on disk", updated, missing)
    return jsonify(ok=True, updated=updated, missing=missing)


@scan_bp.route("/api/scan/reindex_tags", methods=["POST"])
@login_required
def api_reindex_tags():
    """Force a full re-read of every track's file tags into the DB (title/artist/
    album/ISRC/normalized keys). Needed to pick up tags edited outside the app —
    e.g. ISRCs added by an external tagger — which a normal scan skips when the
    file's size:mtime is unchanged."""
    _check_csrf()
    st = state()
    if st.tagger is None:
        return jsonify(ok=False, error="tagger not available")
    if st.progress and st.progress.is_scanning:
        return jsonify(ok=False, error="cannot reindex while a scan is running")
    cleared = st.db.clear_tag_index()

    def _run():
        updated = st.tagger.index_tags()
        log.info("Manual tag reindex: cleared %d, re-read %d", cleared, updated)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify(ok=True, cleared=cleared)


@scan_bp.route("/api/scan/pause", methods=["POST"])
@login_required
def api_scan_pause():
    _check_csrf()
    st = state()
    if st.tagger is None:
        return jsonify(ok=False, error="tagger not available")
    st.tagger._pause_event.clear()
    if st.progress:
        st.progress.set_paused(True)
    return jsonify(ok=True)


@scan_bp.route("/api/scan/resume", methods=["POST"])
@login_required
def api_scan_resume():
    _check_csrf()
    st = state()
    if st.tagger is None:
        return jsonify(ok=False, error="tagger not available")
    st.tagger._pause_event.set()
    if st.progress:
        st.progress.set_paused(False)
    return jsonify(ok=True)


@scan_bp.route("/api/scan/stop", methods=["POST"])
@login_required
def api_scan_stop():
    _check_csrf()
    st = state()
    if st.tagger is None:
        return jsonify(ok=False, error="tagger not available")
    if st.progress and st.progress.is_scanning:
        st.progress.set_stopping(True)
    st.tagger._stop_event.set()
    st.tagger._pause_event.set()  # unblock if currently paused
    return jsonify(ok=True)


@scan_bp.route("/api/restart", methods=["POST"])
@login_required
def api_restart():
    _check_csrf()
    st = state()
    if st.restarting:
        return jsonify(ok=True)
    st.restarting = True
    if st.tagger is not None and st.progress and st.progress.is_scanning:
        st.progress.set_stopping(True)
        st.tagger._stop_event.set()
        st.tagger._pause_event.set()  # unblock if paused

    # Ask the grabber threads to stop; startup-recovery resets any in-flight rows.
    grabber = getattr(st.tagger, "grabber", None) if st.tagger else None
    if grabber is not None:
        try:
            grabber.stop_background()
        except Exception:
            pass

    def _do_restart():
        # Give the grabber pool a moment to wind down (<=5s), then replace process.
        if grabber is not None:
            try:
                grabber.pool.join(5)
            except Exception:
                pass
        time.sleep(1.5)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    threading.Thread(target=_do_restart, daemon=True).start()
    log.info("UI: restart requested — replacing process in 1.5 s")
    return jsonify(ok=True)
