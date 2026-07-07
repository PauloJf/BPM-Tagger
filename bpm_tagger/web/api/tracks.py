"""Track data + per-track mutation endpoints (save/unlock/approve/waveform)."""

import json
import logging
import os
import threading
from pathlib import Path

from flask import Blueprint, Response, abort, jsonify, request

from ...bpm.tags import get_file_hash, write_bpm_tag
from ...bpm.waveform import compute_waveform_peaks
from ...grabber.matching import normalize_artist, normalize_title
from ...grabber.path_template import render, unique_path
from ...grabber.tagging import embed_cover, read_cover, resize_cover, write_track_tags
from ..auth import _check_csrf, login_required
from ..state import _assert_in_music_dir, state

log = logging.getLogger(__name__)

tracks_bp = Blueprint("api_tracks", __name__)


def _parse_bpm_filter(args) -> tuple:
    """Return (bpm_target, bpm_tol) from request args, or (None, 5)."""
    bpm_target = None
    bpm_tol = 5.0
    bpm_str = args.get("bpm", "").strip()
    if bpm_str:
        try:
            bpm_target = float(bpm_str)
            bpm_tol = max(0.0, float(args.get("bpm_tol", "5")))
        except (ValueError, TypeError):
            pass
    return bpm_target, bpm_tol


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
            write_bpm_tag(file_path, bpm, st.preserve_mtime)
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


@tracks_bp.route("/api/duplicates")
@login_required
def api_duplicates():
    """Groups of library tracks sharing normalized artist+title (possible dupes)."""
    return jsonify(groups=state().db.get_duplicates())


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


def _int_or_none(v):
    try:
        return int(str(v).split("/")[0].strip()) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


@tracks_bp.route("/api/track/tags", methods=["PUT"])
@login_required
def api_track_tags():
    """Rewrite descriptive tags; optionally rename to the path template. The DB
    hash is refreshed AFTER all writes so the watcher won't re-analyze the file."""
    _check_csrf()
    st = state()
    data = request.get_json(force=True, silent=True) or {}
    path = data.get("file_path", "")
    if not path:
        return jsonify(ok=False, error="file_path required"), 400
    _assert_in_music_dir(path)
    track = st.db.get_track(path)
    if not track:
        return jsonify(ok=False, error="not found"), 404

    tags = {
        "title": (data.get("title") or "").strip() or None,
        "artist": (data.get("artist") or "").strip() or None,
        "album": (data.get("album") or "").strip() or None,
        "album_artist": (data.get("album_artist") or "").strip() or None,
        "track_no": _int_or_none(data.get("track_no")),
        "disc_no": _int_or_none(data.get("disc_no")),
        "year": _int_or_none(data.get("year")),
        "isrc": (data.get("isrc") or "").strip() or None,
    }
    tags["norm_title"] = normalize_title(tags["title"])
    tags["norm_artist"] = normalize_artist(tags["artist"])

    try:
        write_track_tags(path, tags)
        new_path = path
        if data.get("apply_template"):
            ext = os.path.splitext(path)[1].lstrip(".")
            template = st.config.get("path_template", "{AlbumArtist}/{Album}/{TrackNo:02d} - {Title}.{ext}")
            candidate = unique_path(st.music_dir, render(template, tags, ext))
            if os.path.abspath(candidate) != os.path.abspath(path):
                os.makedirs(os.path.dirname(candidate), exist_ok=True)
                os.replace(path, candidate)  # same filesystem (both under music_dir)
                new_path = candidate
        fresh_hash = get_file_hash(new_path)
        st.db.update_track_metadata(path, new_path, tags, fresh_hash)
        log.info("UI: edited tags for %s", Path(new_path).name)
        return jsonify(ok=True, file_path=new_path)
    except Exception as exc:
        log.error("Tag edit failed: %s", exc)
        return jsonify(ok=False, error=str(exc)), 500


@tracks_bp.route("/api/track/cover")
@login_required
def api_track_cover_get():
    path = request.args.get("path", "")
    if not path:
        abort(400)
    _assert_in_music_dir(path)
    cover = read_cover(path)
    if not cover:
        abort(404)
    data, mime = cover
    return Response(data, mimetype=mime, headers={"Cache-Control": "no-cache"})


@tracks_bp.route("/api/track/cover", methods=["PUT"])
@login_required
def api_track_cover_put():
    _check_csrf()
    st = state()
    path = request.args.get("path", "")
    if not path:
        return jsonify(ok=False, error="path required"), 400
    _assert_in_music_dir(path)
    if not st.db.get_track(path):
        return jsonify(ok=False, error="not found"), 404
    image = request.get_data()
    if not image:
        return jsonify(ok=False, error="empty body"), 400
    try:
        image = resize_cover(image)  # normalize to <=1200px JPEG
        embed_cover(path, image, mime="image/jpeg")
        st.db.refresh_track_hash(path, get_file_hash(path))  # hash only; keep tags
        return jsonify(ok=True)
    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 500


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
