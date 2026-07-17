"""Aggregate stats endpoint for the Stats page."""

from flask import Blueprint, jsonify

from ..auth import login_required
from ..state import state

stats_bp = Blueprint("api_stats", __name__)


@stats_bp.route("/api/stats")
@login_required
def api_stats():
    st = state()
    try:
        summary = st.db.get_stats()
        payload = {
            "summary": summary,
            "bpm_distribution": st.db.get_bpm_distribution(),
            "detector_distribution": st.db.get_detector_distribution(),
            "bpm_descriptive": st.db.get_bpm_descriptive(),
            "run": st.db.get_run_stats(),
        }
        if st.config.get("grabber_enabled"):
            payload["grabber"] = _grabber_stats(st)
        return jsonify(**payload)
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
