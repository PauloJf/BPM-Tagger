"""Aggregate stats endpoint for the Stats page."""

import re

from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from ...db.runs import RUN_IDLE_SECONDS, parse_stamp
from ..auth import login_required
from ..state import state

stats_bp = Blueprint("api_stats", __name__)

# Most-played leaderboards page size: /api/stats returns the first page and
# /api/stats/most_played serves the rest, PAGE_SIZE rows per Show-more click.
# The run journal pages the same way.
PAGE_SIZE = 15

# Owner filter values the run endpoints accept: the two synthetic scopes plus a
# real owner key ('admin' | 'guest' | 'player:<id>').
_OWNER_RE = re.compile(r"^(admin|guest|player:\d+)$")
_ALL = "all"
_UNATTRIBUTED = "unattributed"


@stats_bp.route("/api/stats")
@login_required
def api_stats():
    st = state()
    try:
        summary = st.db.get_stats()
        run = st.db.get_run_stats()
        # One extra row per leaderboard tells the UI whether Show more applies,
        # without a separate COUNT query.
        top_tracks = st.db.get_top_tracks(PAGE_SIZE + 1)
        top_artists = st.db.get_top_artists(PAGE_SIZE + 1)
        payload = {
            "summary": summary,
            "bpm_distribution": st.db.get_bpm_distribution(),
            "detector_distribution": st.db.get_detector_distribution(),
            "bpm_descriptive": st.db.get_bpm_descriptive(),
            # Cumulative, account-blind, exactly as before — the "All" scope of
            # the Run mode card. Per-account slices live on /api/stats/run.
            "run": run,
            "run_owners": _run_owner_options(st.db, run),
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


# ── Per-account run stats + the run journal ─────────────────────────────────
# Admin-only by construction: none of these endpoints are in web/app.py's
# default-deny _PLAYER_ALLOWED, so a kiosk (player) session is 403'd.

def _owner_label(owner: str, usernames: dict) -> str:
    if owner == "admin":
        return "Admin"
    if owner == "guest":
        return "Guest"
    if owner.startswith("player:"):
        pid = owner.split(":", 1)[1]
        return usernames.get(pid) or f"Player {pid}"
    return owner


def _usernames(db) -> dict:
    """{player id as str: username} for labelling owner keys. A run belonging to
    a since-deleted player keeps its key and falls back to 'Player <id>' —
    history is never rewritten."""
    return {str(p["id"]): p["username"] for p in db.list_players()}


def _run_owner_options(db, total: dict) -> list[dict]:
    """The owner filter's choices: every account with attributed run data, plus
    an '(unattributed)' bucket when the all-time totals hold more than the
    per-account ones — i.e. runs recorded before attribution existed."""
    usernames = _usernames(db)
    opts = [{"key": o, "label": _owner_label(o, usernames)} for o in db.list_run_owners()]
    if _wall_ms(total) - _wall_ms(db.get_run_stats_attributed()) > 1000:  # >1s unattributed
        opts.append({"key": _UNATTRIBUTED, "label": "(unattributed)"})
    return opts


def _wall_ms(stats: dict) -> float:
    """The one counter the unattributed remainder is judged on (time on feet)."""
    try:
        return float(stats.get("wall_ms") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _source_label(source, playlists: dict) -> str:
    if not source or source == "library":
        return "Library"
    if source == "mine":
        return "My playlists"
    if source.startswith("playlist:"):
        pid = source.split(":", 1)[1]
        return playlists.get(pid) or f"Playlist {pid}"
    return source


def _run_row(r: dict, usernames: dict, playlists: dict) -> dict:
    """One journal row: the stored counters turned into the numbers the card
    shows. Duration spans the first event's coverage through the last event, so
    an unclosed run still reads honestly (it ended when its events stopped)."""
    started = parse_stamp(r.get("started_at"))
    last = parse_stamp(r.get("last_event_at"))
    end = parse_stamp(r.get("ended_at")) or last
    played = float(r.get("played_ms") or 0.0)
    duration_ms = 0.0
    if started and end:
        duration_ms = max(0.0, (end - started).total_seconds() * 1000.0)
    # A run can never be shorter than the audio time it accumulated (clock skew
    # between devices, a batch flushed late) — the time on feet is the floor.
    duration_ms = max(duration_ms, played)
    stretched = float(r.get("stretched_ms") or 0.0)
    return {
        "id": r["id"],
        "owner": r["owner"],
        "owner_label": _owner_label(r["owner"], usernames),
        "started_at": r.get("started_at"),
        "ended_at": r.get("ended_at") or r.get("last_event_at"),
        "open": r.get("ended_at") is None and _is_live(last),
        "duration_ms": round(duration_ms),
        "played_ms": round(played),
        "source": r.get("source") or "library",
        "source_label": _source_label(r.get("source"), playlists),
        "target_bpm": r.get("target_bpm"),
        "tracks": int(r.get("tracks_played") or 0),
        "avg_cadence": (float(r["cadence_ms_weighted"]) / played) if played > 0 else None,
        "stretched_pct": round((stretched / played) * 100) if played > 0 else 0,
    }


def _is_live(last) -> bool:
    """An open run is only reported as still running while inside the idle
    window — past it, it's a run whose close never arrived."""
    if last is None:
        return False
    return (datetime.now(timezone.utc) - last).total_seconds() <= RUN_IDLE_SECONDS


def _owner_arg():
    """Parse ?owner=: 'all' (default), 'unattributed', or a real owner key.
    Returns (owner, None) or (None, error_response)."""
    raw = (request.args.get("owner") or _ALL).strip()
    if raw in (_ALL, _UNATTRIBUTED) or _OWNER_RE.match(raw):
        return raw, None
    return None, (jsonify(error="owner must be all, unattributed, or an owner key"), 400)


@stats_bp.route("/api/stats/run")
@login_required
def api_stats_run():
    """The Run mode card's counters for one owner scope.

    ``all`` returns the untouched all-time totals (identical to /api/stats'
    ``run``), so the default view can never drift from what it showed before
    attribution existed. A concrete owner returns that account's totals, which
    only cover runs recorded since the upgrade. ``unattributed`` is the honest
    remainder — all-time minus everything attributed — i.e. pre-upgrade history.
    """
    st = state()
    owner, err = _owner_arg()
    if err:
        return err
    try:
        if owner == _ALL:
            return jsonify(owner=owner, run=st.db.get_run_stats(), attributed=False)
        if owner == _UNATTRIBUTED:
            total = st.db.get_run_stats()
            attributed = st.db.get_run_stats_attributed()
            rest = {k: v - float(attributed.get(k) or 0.0) for k, v in total.items()}
            return jsonify(owner=owner, attributed=False,
                           run={k: v for k, v in rest.items() if v > 0})
        return jsonify(owner=owner, run=st.db.get_run_stats_for_owner(owner), attributed=True)
    except Exception as exc:
        return jsonify(error=str(exc)), 500


@stats_bp.route("/api/stats/runs")
@login_required
def api_stats_runs():
    """A page of the run journal, newest first (Show more pages by offset).

    ``owner`` filters server-side to one account; ``all`` is every account.
    ``unattributed`` has no journal rows by definition (pre-attribution history
    was never recorded per run), so it returns an empty page."""
    st = state()
    owner, err = _owner_arg()
    if err:
        return err
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        return jsonify(error="offset must be an integer"), 400
    if owner == _UNATTRIBUTED:
        return jsonify(items=[], has_more=False)
    try:
        rows = st.db.list_runs(PAGE_SIZE + 1, offset,
                               owner=None if owner == _ALL else owner)
        usernames = _usernames(st.db)
        playlists = {str(k): v for k, v in st.db.playlist_names().items()}
        items = [_run_row(r, usernames, playlists) for r in rows[:PAGE_SIZE]]
        return jsonify(items=items, has_more=len(rows) > PAGE_SIZE)
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
