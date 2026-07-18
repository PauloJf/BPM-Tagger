"""Aggregate stats endpoint for the Stats page."""

from flask import Blueprint, jsonify, request

from ..auth import login_required
from ..state import state

stats_bp = Blueprint("api_stats", __name__)

# Most-played leaderboards page size: /api/stats returns the first page and
# /api/stats/most_played serves the rest, PAGE_SIZE rows per Show-more click.
PAGE_SIZE = 15


@stats_bp.route("/api/stats")
@login_required
def api_stats():
    st = state()
    try:
        summary = st.db.get_stats()
        # One extra row per leaderboard tells the UI whether Show more applies,
        # without a separate COUNT query.
        top_tracks = st.db.get_top_tracks(PAGE_SIZE + 1)
        top_artists = st.db.get_top_artists(PAGE_SIZE + 1)
        payload = {
            "summary": summary,
            "bpm_distribution": st.db.get_bpm_distribution(),
            "detector_distribution": st.db.get_detector_distribution(),
            "bpm_descriptive": st.db.get_bpm_descriptive(),
            "run": st.db.get_run_stats(),
            "top_tracks": top_tracks[:PAGE_SIZE],
            "top_artists": top_artists[:PAGE_SIZE],
            "top_tracks_more": len(top_tracks) > PAGE_SIZE,
            "top_artists_more": len(top_artists) > PAGE_SIZE,
            "total_plays": st.db.get_total_plays(),
        }
        if st.config.get("grabber_enabled"):
            payload["grabber"] = _grabber_stats(st)
        return jsonify(**payload)
    except Exception as exc:
        return jsonify(error=str(exc)), 500


@stats_bp.route("/api/stats/most_played")
@login_required
def api_most_played():
    """One further page of a most-played leaderboard (Show more on Stats).

    Query params: kind=artists|tracks, offset (row to start at). Returns
    PAGE_SIZE items plus has_more for the next click.
    """
    st = state()
    kind = request.args.get("kind", "tracks")
    if kind not in ("tracks", "artists"):
        return jsonify(error="kind must be 'tracks' or 'artists'"), 400
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        return jsonify(error="offset must be an integer"), 400
    try:
        fetch = st.db.get_top_tracks if kind == "tracks" else st.db.get_top_artists
        rows = fetch(PAGE_SIZE + 1, offset)
        return jsonify(items=rows[:PAGE_SIZE], has_more=len(rows) > PAGE_SIZE)
    except Exception as exc:
        return jsonify(error=str(exc)), 500


def _grabber_stats(st) -> dict:
    """Library-source rollup shown on Stats when the grabber is on."""
    sources = st.db.get_source_stats()
    dup_groups = st.db.get_duplicates()
    playlists = st.db.list_playlists()
    return {
        "managed": sources["managed"],
        "unmanaged": sources["unmanaged"],
        "grabbed_total": st.db.get_grabbed_total(),
        "providers": sources["providers"],
        "queue": st.db.get_queue_counts(),
        "duplicate_groups": len(dup_groups),
        "duplicate_tracks": sum(g.get("count", 0) for g in dup_groups),
        "playlists": {
            "total": len(playlists),
            "watched": sum(1 for p in playlists if p.get("enabled")),
            "have": sum(p.get("have_count", 0) for p in playlists),
            "missing": sum(p.get("missing_count", 0) for p in playlists),
            "queued": sum(p.get("queued_count", 0) for p in playlists),
        },
    }
