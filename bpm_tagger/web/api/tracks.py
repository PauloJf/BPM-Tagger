"""Track data + per-track mutation endpoints (save/unlock/approve/waveform)."""

import json
import logging
import threading
from pathlib import Path

from flask import Blueprint, abort, jsonify, request

from ...bpm.tags import write_bpm_tag
from ...bpm.waveform import compute_waveform_peaks
from ..auth import _check_csrf, login_required
from ..pages import _parse_bpm_filter
from ..state import _assert_in_music_dir, state

log = logging.getLogger(__name__)

tracks_bp = Blueprint("api_tracks", __name__)


@tracks_bp.route("/api/save_bpm", methods=["POST"])
@login_required
def api_save_bpm():
    _check_csrf()
    st = state()
    data = request.get_json(force=True, silent=True) or {}
    file_path = data.get("file_path", "")
    try:
        bpm = float(data["bpm"])
    except (KeyError, ValueError, TypeError):
        return jsonify(ok=False, error="bpm must be a number")

    if not file_path:
        return jsonify(ok=False, error="file_path required")

    _assert_in_music_dir(file_path)

    try:
        st.db.lock_track(file_path, bpm)
        if st.write_tags:
            write_bpm_tag(file_path, bpm)
        log.info("UI: locked %s at %.1f BPM", Path(file_path).name, bpm)
        return jsonify(ok=True)
    except Exception as exc:
        log.error("UI save_bpm error: %s", exc)
        return jsonify(ok=False, error=str(exc))


@tracks_bp.route("/api/unlock", methods=["POST"])
@login_required
def api_unlock():
    _check_csrf()
    st = state()
    data = request.get_json(force=True, silent=True) or {}
    file_path = data.get("file_path", "")
    if not file_path:
        return jsonify(ok=False, error="file_path required")

    _assert_in_music_dir(file_path)

    try:
        st.db.unlock_track(file_path)
        log.info("UI: unlocked %s", Path(file_path).name)
        return jsonify(ok=True)
    except Exception as exc:
        return jsonify(ok=False, error=str(exc))


@tracks_bp.route("/api/approve", methods=["POST"])
@login_required
def api_approve():
    _check_csrf()
    st = state()
    data = request.get_json(force=True, silent=True) or {}
    file_path = data.get("file_path", "")
    if not file_path:
        return jsonify(ok=False, error="file_path required")
    _assert_in_music_dir(file_path)
    try:
        st.db.approve_track(file_path)
        log.info("UI: approved %s", Path(file_path).name)
        return jsonify(ok=True, review_count=st.db.get_stats().get("needs_review", 0))
    except Exception as exc:
        return jsonify(ok=False, error=str(exc))


@tracks_bp.route("/api/waveform")
@login_required
def api_waveform():
    st = state()
    path = request.args.get("path", "")
    if not path:
        abort(400)
    _assert_in_music_dir(path)

    # 1. In-memory cache (fastest)
    if path in st.waveform_cache:
        return jsonify(st.waveform_cache[path])

    # 2. DB — populated during BPM analysis so no extra librosa call needed
    if st.db:
        track = st.db.get_track(path)
        if track and track.get("waveform_peaks"):
            try:
                result = json.loads(track["waveform_peaks"])
                st.cache_waveform(path, result)
                return jsonify(result)
            except Exception:
                pass  # corrupt value — fall through to recompute

    # 3. Deduplicated librosa fallback (old tracks not yet in DB)
    #    Only one thread computes per path; others wait on the Event.
    with st.waveform_inflight_lock:
        if path in st.waveform_inflight:
            ev = st.waveform_inflight[path]
            leader = False
        else:
            ev = threading.Event()
            st.waveform_inflight[path] = ev
            leader = True

    if not leader:
        ev.wait(timeout=30)
        result = st.waveform_cache.get(path)
        if result:
            return jsonify(result)
        return jsonify(error="waveform not available"), 503

    try:
        raw = compute_waveform_peaks(path)
        if raw is None:
            return jsonify(error="waveform computation failed"), 500
        result = json.loads(raw)
        st.cache_waveform(path, result)
        if st.db:
            st.db.save_waveform_peaks(path, raw)
        return jsonify(result)
    except Exception as exc:
        log.warning("Waveform generation failed for %s: %s", Path(path).name, exc)
        return jsonify(error=str(exc)), 500
    finally:
        ev.set()
        with st.waveform_inflight_lock:
            st.waveform_inflight.pop(path, None)


@tracks_bp.route("/api/track")
@login_required
def api_track():
    """Single-track detail for the SPA TrackDetail page.

    Mirrors ``pages.track_detail``: detector values, confidence and lock state
    come straight from the row; prev/next are resolved within the review queue
    (``back=review``). Track-list navigation is handled client-side from the
    already-loaded page, matching the current Jinja UI.
    """
    st = state()
    path = request.args.get("path", "")
    if not path:
        abort(400)
    _assert_in_music_dir(path)
    track = st.db.get_track(path)
    if not track:
        abort(404)

    back = request.args.get("back", "tracks")
    if back not in ("tracks", "review"):
        back = "tracks"

    prev_path = next_path = None
    queue_pos = queue_total = None
    if back == "review":
        queue = [t["file_path"] for t in
                 st.db.get_suspicious(st.conf_threshold, 0, float("inf"))]
        queue_total = len(queue)
        try:
            idx = queue.index(path)
            queue_pos = idx + 1
            prev_path = queue[idx - 1] if idx > 0 else None
            next_path = queue[idx + 1] if idx < len(queue) - 1 else None
        except ValueError:
            pass

    return jsonify(track=track, back=back,
                   prev_path=prev_path, next_path=next_path,
                   queue_pos=queue_pos, queue_total=queue_total,
                   playback_buffer=st.config.get("playback_buffer", 3))


@tracks_bp.route("/api/review")
@login_required
def api_review():
    """Paginated review queue (suspicious tracks) for the SPA Review page."""
    st = state()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    per_page = 50
    total = st.db.get_suspicious_count(st.conf_threshold, st.bpm_min, st.bpm_max)
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    rows = st.db.get_suspicious_page(st.conf_threshold, st.bpm_min, st.bpm_max,
                                     per_page, (page - 1) * per_page)
    return jsonify(tracks=rows, conf_threshold=st.conf_threshold,
                   total=total, page=page, pages=pages, per_page=per_page)


@tracks_bp.route("/api/tracks")
@login_required
def api_tracks():
    st = state()
    q = request.args.get("q", "").strip()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        per_page = int(request.args.get("per_page", 50))
        if per_page not in (10, 50, 100):
            per_page = 50
    except (ValueError, TypeError):
        per_page = 50
    filter_by = request.args.get("filter", "")
    bpm_target, bpm_tol = _parse_bpm_filter(request.args)
    rows, total = st.db.get_tracks_page(q, per_page, (page - 1) * per_page,
                                        filter=filter_by,
                                        bpm_target=bpm_target, bpm_tol=bpm_tol)
    pages = max(1, (total + per_page - 1) // per_page)
    stats = st.db.get_stats()
    return jsonify(tracks=rows, total=total, page=page, pages=pages, per_page=per_page,
                   filter=filter_by,
                   all_count=stats.get("total", 0),
                   review_count=stats.get("needs_review", 0),
                   locked_count=stats.get("locked", 0),
                   deleted_count=stats.get("deleted", 0))
