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
        return jsonify(
            summary=summary,
            bpm_distribution=st.db.get_bpm_distribution(),
            detector_distribution=st.db.get_detector_distribution(),
            bpm_descriptive=st.db.get_bpm_descriptive(),
        )
    except Exception as exc:
        return jsonify(error=str(exc)), 500
