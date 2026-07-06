"""Ambiguity inbox (§3): resolve awaiting_user grab items.

choose/search set the resolution fields and return the item to 'pending' so a
GrabWorker resumes it (choose → download that candidate; search → re-search with
the user's query). skip marks it skipped.
"""

import logging

from flask import Blueprint, jsonify, request

from ..auth import _check_csrf, login_required
from ..state import state

log = logging.getLogger(__name__)

inbox_bp = Blueprint("api_inbox", __name__)


def _grabber():
    st = state()
    return getattr(st.tagger, "grabber", None) if st.tagger else None


def _nudge():
    g = _grabber()
    if g:
        g.sync.request_sync()  # workers poll independently; harmless nudge


@inbox_bp.route("/api/inbox")
@login_required
def list_inbox():
    db = state().db
    items = db.get_queue("awaiting_user")
    for it in items:
        it["candidates"] = db.get_grab_candidates(it["id"])
    return jsonify(items=items)


def _item_or_404(item_id):
    item = state().db.get_grab_item(item_id)
    if not item:
        return None, (jsonify(error="not_found"), 404)
    if item["status"] != "awaiting_user":
        return None, (jsonify(error="not_awaiting", status=item["status"]), 400)
    return item, None


@inbox_bp.route("/api/inbox/<int:item_id>/choose", methods=["POST"])
@login_required
def choose(item_id):
    _check_csrf()
    db = state().db
    item, err = _item_or_404(item_id)
    if err:
        return err
    data = request.get_json(force=True, silent=True) or {}
    cand_id = data.get("candidate_id")
    cand = db.get_grab_candidate(cand_id) if cand_id else None
    if not cand or cand["queue_item_id"] != item_id:
        return jsonify(error="invalid_candidate"), 400
    db.update_grab(item_id, chosen_candidate_id=cand_id, search_override=None)
    db.transition(item_id, "pending", f"chose candidate #{cand_id}")
    _nudge()
    return jsonify(ok=True)


@inbox_bp.route("/api/inbox/<int:item_id>/search", methods=["POST"])
@login_required
def search(item_id):
    _check_csrf()
    db = state().db
    item, err = _item_or_404(item_id)
    if err:
        return err
    query = str((request.get_json(force=True, silent=True) or {}).get("query", "")).strip()
    if not query:
        return jsonify(error="query_required"), 400
    db.update_grab(item_id, search_override=query, chosen_candidate_id=None)
    db.transition(item_id, "pending", f"re-search: {query}")
    _nudge()
    return jsonify(ok=True)


@inbox_bp.route("/api/inbox/<int:item_id>/skip", methods=["POST"])
@login_required
def skip(item_id):
    _check_csrf()
    db = state().db
    item, err = _item_or_404(item_id)
    if err:
        return err
    db.transition(item_id, "skipped", "skipped from inbox")
    return jsonify(ok=True)
