"""Media streaming, progress, health, and version-check endpoints."""

import json
import logging
import os
import urllib.error
import urllib.request

from flask import Blueprint, abort, jsonify, request, send_file, session

from ..auth import _check_csrf, login_required, session_owner
from ..state import _assert_in_music_dir, state

log = logging.getLogger(__name__)

media_bp = Blueprint("media", __name__)


@media_bp.route("/api/scrobble", methods=["POST"])
@login_required
def api_scrobble():
    """Report a play from the built-in player (the client posts once per track,
    at the halfway mark).

    The play is ALWAYS counted locally (+1), so play counts work and persist even
    with Navidrome disconnected or never configured. When Navidrome scrobbling is
    enabled the play is additionally forwarded — which also feeds
    Last.fm/ListenBrainz if wired up there — and the next play-count pull merges
    the remote total back in with MAX(), so a forwarded play is never
    double-counted and a local play is never lost. Forwarding is best-effort: an
    unmatched track or a rejected scrobble does not undo the local count, so the
    response is always ok=True (with `forwarded` telling whether it reached
    Navidrome). The client can fire and forget.

    The same report is ALSO attributed to the session's account as a play event
    (with the run's cadence when the player was tempo-locked) — additive to, and
    never a substitute for, the library-global play count above."""
    _check_csrf()
    st = state()
    cfg = st.config

    body = request.get_json(silent=True) or {}
    path = str(body.get("path", ""))
    _assert_in_music_dir(path)
    track = st.db.get_track(path)
    if not track:
        abort(404)

    # Local tally first — independent of Navidrome.
    st.db.bump_play_count(path)
    _record_play_event(st, body, path)

    url = str(cfg.get("navidrome_url", "")).rstrip("/")
    user = str(cfg.get("navidrome_user", ""))
    pwd = str(cfg.get("navidrome_pass", ""))
    if not (cfg.get("navidrome_scrobble") and url and user and pwd):
        return jsonify(ok=True, forwarded=False)

    from ...integrations.navidrome import resolve_id, scrobble
    sid = track.get("nd_song_id")
    if not sid:
        sid = resolve_id(url, user, pwd, track)
        if sid:
            st.db.set_nd_song_id(path, sid)
    if not sid:
        return jsonify(ok=True, forwarded=False, forward_error="track not matched in Navidrome")
    if not scrobble(url, user, pwd, sid):
        return jsonify(ok=True, forwarded=False, forward_error="Navidrome rejected the scrobble")
    return jsonify(ok=True, forwarded=True)


def _record_play_event(st, body: dict, path: str) -> None:
    """Per-account attribution for one scrobble (see db/runs.py).

    A handful of writes at most (an INSERT, plus the account's open-run lookup —
    and the run's own INSERT when this play is the first event of a run, which a
    short track's halfway report can be) and never fatal: attribution must not be
    able to fail a play report or the Navidrome forward that follows it.

    The run context (``{source, target, stretched}``) is passed through so the
    run lifecycle sees it exactly as it sees a run-stat flush's."""
    ctx = body.get("run") if isinstance(body.get("run"), dict) else None
    cadence = None
    if ctx is not None:
        try:
            cadence = float(ctx.get("target"))
        except (TypeError, ValueError):
            cadence = None
    try:
        duration_ms = int(body["duration_ms"]) if body.get("duration_ms") is not None else None
    except (TypeError, ValueError):
        duration_ms = None
    try:
        st.db.record_play_event(
            session_owner(), path, duration_ms=duration_ms, cadence=cadence,
            stretched=bool(ctx.get("stretched")) if ctx else False,
            run=ctx if cadence is not None else None)
    except Exception as exc:  # pragma: no cover - best effort
        log.debug("play-event attribution failed: %s", exc)


@media_bp.route("/api/progress")
@login_required
def api_progress():
    st = state()
    if st.progress is None:
        return jsonify(is_scanning=False, is_paused=False, is_stopping=False,
                       completed=0, total=0, cumulative_completed=0,
                       current_file="", current_step="",
                       step_index=0, step_total=3, last_file="", last_bpm=None)
    return jsonify(**st.progress.snapshot())


@media_bp.route("/api/version/check")
@login_required
def api_version_check():
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/paulojf/bpm-tagger/releases/latest",
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "bpm-tagger-ui"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        return jsonify(latest=data.get("tag_name", "unknown"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return jsonify(latest=None)
        return jsonify(error=str(exc))
    except Exception as exc:
        return jsonify(error=str(exc))


@media_bp.route("/api/changelog")
@login_required
def api_changelog():
    """Release notes for the admin 'What's new' popup + changelog view. Not in
    the player allowlist, so a kiosk session is refused by the scope gate."""
    from ...config import read_changelog
    return jsonify(changelog=read_changelog())


@media_bp.route("/healthz")
def healthz():
    """Liveness probe. Library stats ride along only for an authenticated admin —
    the bare endpoint stays public for Docker healthchecks, and a player (kiosk)
    session is deliberately not shown library stats (matches /api/me)."""
    st = state()
    try:
        stats = (st.db.get_stats() if st.db and session.get("role") == "admin" else {})
        return jsonify(status="ok", **stats)
    except Exception as exc:
        return jsonify(status="error", error=str(exc)), 500


@media_bp.route("/audio")
@login_required
def audio():
    # NOTE (Phase 5): playlist association is an *organizational* boundary, not a
    # security one. A player scoped to playlist X can still stream any path under
    # MUSIC_DIR if they learn/guess it — streaming is gated by path-validation alone,
    # NOT by playlist membership. This is intentional: Run-mode curation decides what
    # a user is *offered*, not a DRM wall around the bytes.
    file_path = request.args.get("path", "")
    if not file_path:
        abort(400)
    real = _assert_in_music_dir(file_path)
    if not os.path.isfile(real):
        abort(404)
    return send_file(real, conditional=True)
