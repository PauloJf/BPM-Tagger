"""GET /api/stats/summary: the read-only dashboard feed (Glance's custom-api and the like).

Token only: ``Authorization: Bearer <STATS_API_TOKEN>``. No login session opens it and it opens nothing else.
With STATS_API_TOKEN empty the route answers 404, as if it did not exist.
"""

import hmac
import os
import re
import threading
import time

from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from ...db.stats_summary import STATS_TZ, WINDOWS, window_starts
from ..state import state

stats_summary_bp = Blueprint("api_stats_summary", __name__)

_OWNER_RE = re.compile(r"^(admin|guest|player:\d+)$")
_FAIL_MAX, _FAIL_WINDOW = 10, 60      # wrong tokens per address per minute before a 429, as the login does


class _Throttle:
    def __init__(self):
        self._fails: dict = {}
        self._lock = threading.Lock()

    def retry_after(self, who: str, now: float) -> int:
        with self._lock:
            recent = [t for t in self._fails.get(who, []) if now - t < _FAIL_WINDOW]
            if recent:
                self._fails[who] = recent
            else:
                self._fails.pop(who, None)
            return max(1, int(recent[0] + _FAIL_WINDOW - now) + 1) if len(recent) >= _FAIL_MAX else 0

    def fail(self, who: str, now: float) -> None:
        with self._lock:
            if len(self._fails) > 4096:
                self._fails.clear()
            self._fails.setdefault(who, []).append(now)


_throttle = _Throttle()


def _num(x, nd=1):
    return round(float(x), nd)


@stats_summary_bp.route("/api/stats/summary")
def api_stats_summary():
    st = state()
    token = str(st.config.get("stats_api_token", "") or "")
    if not token:
        return jsonify(error="not_found"), 404
    who, now_t = request.remote_addr or "?", time.time()
    wait = _throttle.retry_after(who, now_t)
    if wait:
        return jsonify(error="too_many_attempts"), 429, {"Retry-After": str(wait)}
    header = request.headers.get("Authorization", "")
    supplied = header[7:] if header.lower().startswith("bearer ") else ""
    if not (supplied and hmac.compare_digest(supplied.encode(), token.encode())):
        _throttle.fail(who, now_t)
        return jsonify(error="unauthorized"), 401, {"WWW-Authenticate": "Bearer"}

    owner = request.args.get("owner", "").strip()
    if owner and not _OWNER_RE.match(owner):
        return jsonify(error="bad_owner", message="owner is admin, guest or player:<id>"), 400

    db = st.db
    now = datetime.now(timezone.utc)
    starts = {k: v.isoformat() for k, v in window_starts(now).items()}
    lib = db.stats_library_totals()
    tag = db.stats_tagging_state()
    listen = db.stats_listening(starts, owner or None)
    top = db.stats_top_since(starts["month"], owner or None)

    out = {
        "generated_at": now.isoformat(),
        "timezone": getattr(STATS_TZ, "key", "UTC"),
        "library_tracks": lib["tracks"],
        "library_artists": lib["artists"],
        "library_albums": lib["albums"],
        "library_size_bytes": lib["size_bytes"],
        "library_minutes": _num(lib["duration_ms"] / 60000.0),
        "tracks_analysed": tag["analysed"],
        "tracks_pending": tag["pending"],
        "tracks_errors": tag["errors"],
        "tracks_needs_review": tag["needs_review"],
    }
    for w in WINDOWS:
        out[f"plays_{w}"] = listen[w]["plays"]
        out[f"minutes_{w}"] = _num(listen[w]["minutes"])
        out[f"artists_{w}"] = listen[w]["artists"]
    a, t = top["artist"], top["track"]
    out["top_artist_month"] = (a or {}).get("name") or ""
    out["top_artist_month_plays"] = (a or {}).get("plays", 0)
    out["top_track_month"] = (t or {}).get("title") or (os.path.splitext(os.path.basename(t["path"]))[0] if t else "")
    out["top_track_month_artist"] = (t or {}).get("artist") or ""
    out["top_track_month_plays"] = (t or {}).get("plays", 0)
    if owner:
        out["owner"] = owner
    if request.args.get("by_owner", "").lower() in ("1", "true", "yes"):
        out["owners"] = db.stats_listening_by_owner(starts)
    return jsonify(out), 200, {"Cache-Control": "no-store"}
