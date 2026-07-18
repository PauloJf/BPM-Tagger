"""Media streaming, progress, health, and version-check endpoints."""

import json
import logging
import os
import urllib.error
import urllib.request

from flask import Blueprint, abort, jsonify, request, send_file, session

from ..auth import _check_csrf, login_required
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
    Navidrome). The client can fire and forget."""
    _check_csrf()
    st = state()
    cfg = st.config

    path = str((request.get_json(silent=True) or {}).get("path", ""))
    _assert_in_music_dir(path)
    track = st.db.get_track(path)
    if not track:
        abort(404)

    # Local tally first — independent of Navidrome.
    st.db.bump_play_count(path)

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
    file_path = request.args.get("path", "")
    if not file_path:
        abort(400)
    real = _assert_in_music_dir(file_path)
    if not os.path.isfile(real):
        abort(404)
    return send_file(real, conditional=True)
