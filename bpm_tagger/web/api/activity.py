"""One poll for "what is the app doing in the background?" (the sidebar's activity list).

The scan has its own card (``/api/progress``). This lists everything else that can run
for a while: the bulk fills (loudness, waveforms, lyrics, ISRC), which keep their counters
in their own modules, and the Navidrome syncs, which register in ``bpm_tagger.activity``.
Admin-only: it is not on the player allowlist.
"""

from flask import Blueprint, jsonify

from ... import activity
from ..auth import login_required

activity_bp = Blueprint("api_activity", __name__)


def _fills() -> list[dict]:
    # Imported here: these modules import this package's siblings at load time.
    from . import loudness, lyrics, tracks, waveform
    out = []
    for key, label, lock, fill in (
            ("loudness", "Measuring loudness", loudness._fill_lock, loudness._fill),
            ("waveform", "Building waveforms", waveform._fill_lock, waveform._fill),
            ("lyrics", "Fetching lyrics", lyrics._fill_lock, lyrics._fill),
            ("isrc", "Looking up ISRCs", tracks._isrc_fill_lock, tracks._isrc_fill)):
        with lock:
            if not fill.get("running"):
                continue
            total, done = int(fill.get("total") or 0), int(fill.get("done") or 0)
        out.append({"id": key, "label": label, "done": done,
                    "total": total or None})     # None: still counting, show no bar
    return out


@activity_bp.route("/api/activity")
@login_required
def api_activity():
    items = _fills() + [{"id": a["id"], "label": a["label"], "done": None, "total": None}
                        for a in activity.snapshot()]
    return jsonify(items=items)
