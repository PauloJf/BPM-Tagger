"""Grab-queue endpoints (§3): list, detail (candidates+events), retry/cancel/
priority, history, and enqueue-missing for a playlist."""

import logging

from flask import Blueprint, jsonify, request

from ...db import GRAB_TERMINAL
from ..auth import _check_csrf, login_required
from ..state import state

log = logging.getLogger(__name__)

queue_bp = Blueprint("api_queue", __name__)


def _grabber():
    st = state()
    return getattr(st.tagger, "grabber", None) if st.tagger else None


@queue_bp.route("/api/queue")
@login_required
def list_queue():
    status = request.args.get("status", "")
    return jsonify(items=state().db.get_queue(status),
                   counts=state().db.get_queue_counts())


@queue_bp.route("/api/queue/history")
@login_required
def queue_history():
    return jsonify(items=state().db.get_grab_history())


@queue_bp.route("/api/queue/<int:item_id>")
@login_required
def queue_item(item_id):
    db = state().db
    item = db.get_grab_item(item_id)
    if not item:
        return jsonify(error="not_found"), 404
    return jsonify(item=item,
                   candidates=db.get_grab_candidates(item_id),
                   events=db.get_grab_events(item_id))


@queue_bp.route("/api/queue/<int:item_id>/retry", methods=["POST"])
@login_required
def retry_item(item_id):
    _check_csrf()
    db = state().db
    item = db.get_grab_item(item_id)
    if not item:
        return jsonify(error="not_found"), 404
    if item["status"] not in ("failed", "skipped"):
        return jsonify(error="not_retryable", status=item["status"]), 400
    db.update_grab(item_id, error=None, progress=0)
    db.transition(item_id, "pending", "retry")
    g = _grabber()
    if g:
        g.sync.request_sync()  # nudge (workers poll independently)
    return jsonify(ok=True)


@queue_bp.route("/api/queue/<int:item_id>/cancel", methods=["POST"])
@login_required
def cancel_item(item_id):
    _check_csrf()
    db = state().db
    item = db.get_grab_item(item_id)
    if not item:
        return jsonify(error="not_found"), 404
    if item["status"] in GRAB_TERMINAL:
        return jsonify(error="already_terminal", status=item["status"]), 400
    db.transition(item_id, "skipped", "cancelled")
    return jsonify(ok=True)


@queue_bp.route("/api/queue/<int:item_id>/priority", methods=["POST"])
@login_required
def set_priority(item_id):
    _check_csrf()
    data = request.get_json(force=True, silent=True) or {}
    try:
        pri = int(data.get("priority", 0))
    except (ValueError, TypeError):
        pri = 0
    state().db.update_grab(item_id, priority=pri)
    return jsonify(ok=True)


@queue_bp.route("/api/playlists/<int:pid>/enqueue-missing", methods=["POST"])
@login_required
def enqueue_missing(pid):
    _check_csrf()
    g = _grabber()
    if not g:
        return jsonify(error="grabber_disabled"), 409
    if not state().db.get_playlist(pid):
        return jsonify(error="not_found"), 404
    n = g.enqueue_missing(pid)
    return jsonify(ok=True, enqueued=n)
