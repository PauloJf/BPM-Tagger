"""Ambiguity inbox (§3): resolve awaiting_user grab items.

choose/search set the resolution fields and return the item to 'pending' so a
GrabWorker resumes it (choose → download that candidate; search → re-search with
the user's query). skip marks it skipped.
"""

import logging
import threading
import time

from flask import Blueprint, jsonify, request

from ...integrations import deezer_catalog
from ..auth import _check_csrf, login_required
from ..state import state

log = logging.getLogger(__name__)

inbox_bp = Blueprint("api_inbox", __name__)

# Lazy preview-URL resolution: Deezer preview links (cdns-preview-*.dzcdn.net) are
# not immortal, so we resolve them on first click rather than storing them at search
# time — and cache the result briefly so repeated clicks / re-renders don't re-hit
# Deezer. Same lock-guarded, oldest-expiring-eviction style as the related cache in
# web/api/suggestions.py. Empty results are cached too, so a track with no preview
# isn't re-queried on every click.
_PREVIEW_TTL = 3600.0          # seconds; Deezer preview URLs live comfortably longer
_PREVIEW_CACHE_MAX = 500
_cand_preview_cache: dict[int, tuple[float, str]] = {}   # cand_id -> (expires_at, url)
_preview_lock = threading.Lock()


def _cache_get(cache: dict, key) -> "str | None":
    with _preview_lock:
        ent = cache.get(key)
        if not ent:
            return None
        expires_at, url = ent
        if expires_at < time.monotonic():
            cache.pop(key, None)
            return None
        return url


def _cache_put(cache: dict, key, url: str) -> None:
    with _preview_lock:
        if len(cache) >= _PREVIEW_CACHE_MAX and key not in cache:
            oldest = min(cache, key=lambda k: cache[k][0])
            cache.pop(oldest, None)
        cache[key] = (time.monotonic() + _PREVIEW_TTL, url)


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


@inbox_bp.route("/api/inbox/<int:item_id>/research", methods=["POST"])
@login_required
def research(item_id):
    """Re-run the default search (item's own metadata), clearing any prior
    override — e.g. to retry after enabling a new provider."""
    _check_csrf()
    db = state().db
    item, err = _item_or_404(item_id)
    if err:
        return err
    db.update_grab(item_id, search_override=None, chosen_candidate_id=None)
    db.transition(item_id, "pending", "search again (original metadata)")
    _nudge()
    return jsonify(ok=True)


@inbox_bp.route("/api/inbox/research-all", methods=["POST"])
@login_required
def research_all():
    """Re-run the default search for every item waiting in the inbox at once
    (e.g. after enabling a new provider)."""
    _check_csrf()
    db = state().db
    n = 0
    for it in db.get_queue("awaiting_user"):
        db.update_grab(it["id"], search_override=None, chosen_candidate_id=None)
        db.transition(it["id"], "pending", "search again (all)")
        n += 1
    _nudge()
    return jsonify(ok=True, requeued=n)


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


@inbox_bp.route("/api/inbox/candidates/<int:cand_id>/preview")
@login_required
def candidate_preview(cand_id):
    """Resolve a Deezer candidate's 30-second preview URL on demand (see the
    lazy-resolution note at the top of the module). GET, no CSRF; the page is
    already behind GrabberGate, so no grabber-enabled check is needed.

    Non-Deezer candidates (yt-dlp etc.) and Deezer tracks with no preview return
    a uniform 200-with-empty, matching the quiet-failure convention of
    /api/related/*."""
    cand = state().db.get_grab_candidate(cand_id)
    if not cand:
        return jsonify(error="not_found"), 404
    dz_id = cand["provider_track_id"] or ""
    if cand["provider"] != "deezer" or not dz_id:
        return jsonify(preview_url="", dz_track_id="")
    url = _cache_get(_cand_preview_cache, cand_id)
    if url is None:
        url = deezer_catalog.track_preview_url(dz_id)
        _cache_put(_cand_preview_cache, cand_id, url)
    return jsonify(preview_url=url, dz_track_id=dz_id)
