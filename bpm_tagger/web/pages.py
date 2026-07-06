"""Jinja page routes (M0 legacy UI; replaced by the React SPA in M2)."""

from flask import Blueprint, abort, redirect, render_template, request, url_for

from ..config import __version__
from .auth import login_required
from .state import state

pages_bp = Blueprint("pages", __name__)


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


@pages_bp.route("/")
@login_required
def index():
    return redirect(url_for("pages.tracks"))


@pages_bp.route("/tracks")
@login_required
def tracks():
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
    return render_template("tracks.html", tracks=rows, total=total, page=page, pages=pages,
                           q=q, per_page=per_page, filter=filter_by,
                           bpm=request.args.get("bpm", ""),
                           bpm_tol=int(bpm_tol),
                           all_count=stats.get("total", 0),
                           review_count=stats.get("needs_review", 0),
                           locked_count=stats.get("locked", 0),
                           deleted_count=stats.get("deleted", 0))


@pages_bp.route("/review")
@login_required
def review():
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
    return render_template("review.html", tracks=rows, conf_threshold=st.conf_threshold,
                           total=total, page=page, pages=pages)


@pages_bp.route("/track")
@login_required
def track_detail():
    st = state()
    path = request.args.get("path", "")
    back = request.args.get("back", "tracks")
    if back not in ("tracks", "review"):
        back = "tracks"
    track = st.db.get_track(path)
    if not track:
        abort(404)

    # Reconstruct the library URL the user came from
    if back == "review":
        back_url = url_for("pages.review")
    else:
        kw = {}
        bf = request.args.get("back_filter", "")
        bp = request.args.get("back_page", "")
        bq = request.args.get("back_q", "")
        bpp = request.args.get("back_per_page", "")
        if bf:  kw["filter"]   = bf
        if bp:  kw["page"]     = bp
        if bq:  kw["q"]        = bq
        if bpp: kw["per_page"] = bpp
        back_url = url_for("pages.tracks", **kw)

    prev_path = next_path = None
    queue_pos = queue_total = None
    if back == "review":
        queue = [t["file_path"] for t in st.db.get_suspicious(st.conf_threshold, 0, float("inf"))]
        queue_total = len(queue)
        try:
            idx = queue.index(path)
            queue_pos = idx + 1
            prev_path = queue[idx - 1] if idx > 0 else None
            next_path = queue[idx + 1] if idx < len(queue) - 1 else None
        except ValueError:
            pass

    return render_template("track.html", track=track, back=back, back_url=back_url,
                           prev_path=prev_path, next_path=next_path,
                           queue_pos=queue_pos, queue_total=queue_total,
                           playback_buffer=st.config.get("playback_buffer", 3))


@pages_bp.route("/stats")
@login_required
def stats():
    return render_template("stats.html")


@pages_bp.route("/about")
@login_required
def about():
    return render_template("about.html", version=__version__)


@pages_bp.route("/settings")
@login_required
def settings():
    return render_template("settings.html", cfg=state().config, version=__version__)
