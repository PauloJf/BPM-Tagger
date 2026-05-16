"""Flask web UI for BPM Tagger — runs as a daemon thread inside the main container."""

import logging
import os
import secrets
import time
from collections import defaultdict
from datetime import timedelta
from functools import wraps
from pathlib import Path
from threading import Lock

from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                   request, send_file, session, url_for)

log = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates")

# Populated by start()
_db = None
_music_dir = ""
_write_tags = True
_conf_threshold = 0.4

# Brute-force login protection
_login_attempts: dict = defaultdict(list)
_login_lock = Lock()
_max_login_attempts = 5
_lockout_seconds = 300
_attempt_window = 60


# ---------------------------------------------------------------------------
# Jinja2 filters
# ---------------------------------------------------------------------------

@app.template_filter("basename")
def _basename(path):
    return Path(path).name

@app.template_filter("dirname")
def _dirname(path):
    return str(Path(path).parent)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("ok"):
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = request.remote_addr or "unknown"
        now = time.time()
        with _login_lock:
            attempts = _login_attempts[ip]
            # Prune entries outside the rolling window
            _login_attempts[ip] = [t for t in attempts if now - t < _attempt_window]
            if len(_login_attempts[ip]) >= _max_login_attempts:
                return render_template("login.html", lockout=True), 429
            if request.form.get("password") == app.config["UI_PASSWORD"]:
                _login_attempts.pop(ip, None)
                session["ok"] = True
                return redirect(request.args.get("next") or url_for("tracks"))
            _login_attempts[ip].append(now)
        flash("Wrong password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.route("/")
@login_required
def index():
    return redirect(url_for("tracks"))


@app.route("/tracks")
@login_required
def tracks():
    q       = request.args.get("q", "").strip()
    page    = max(1, int(request.args.get("page", 1)))
    per_page = 50

    with _db._connect() as conn:
        if q:
            like = f"%{q}%"
            total = conn.execute(
                "SELECT COUNT(*) FROM tracks WHERE file_path LIKE ?", (like,)
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT * FROM tracks WHERE file_path LIKE ? "
                "ORDER BY analyzed_at DESC LIMIT ? OFFSET ?",
                (like, per_page, (page - 1) * per_page)
            ).fetchall()
        else:
            total = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
            rows = conn.execute(
                "SELECT * FROM tracks ORDER BY analyzed_at DESC LIMIT ? OFFSET ?",
                (per_page, (page - 1) * per_page)
            ).fetchall()

    pages = max(1, (total + per_page - 1) // per_page)
    return render_template("tracks.html", tracks=[dict(r) for r in rows],
                           total=total, page=page, pages=pages, q=q)


@app.route("/review")
@login_required
def review():
    rows = _db.get_suspicious(_conf_threshold, 0, float("inf"))
    return render_template("review.html", tracks=rows, conf_threshold=_conf_threshold)


@app.route("/track")
@login_required
def track_detail():
    path = request.args.get("path", "")
    back = request.args.get("back", "tracks")
    if back not in ("tracks", "review"):
        back = "tracks"
    track = _db.get_track(path)
    if not track:
        abort(404)

    prev_path = next_path = None
    queue_pos = queue_total = None
    if back == "review":
        queue = [t["file_path"] for t in _db.get_suspicious(_conf_threshold, 0, float("inf"))]
        queue_total = len(queue)
        if path in queue:
            idx = queue.index(path)
            queue_pos = idx + 1
            prev_path = queue[idx - 1] if idx > 0 else None
            next_path = queue[idx + 1] if idx < len(queue) - 1 else None

    return render_template("track.html", track=track, back=back,
                           prev_path=prev_path, next_path=next_path,
                           queue_pos=queue_pos, queue_total=queue_total)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.route("/api/save_bpm", methods=["POST"])
@login_required
def api_save_bpm():
    from bpm_tagger import write_bpm_tag
    data = request.get_json(force=True, silent=True) or {}
    file_path = data.get("file_path", "")
    try:
        bpm = float(data["bpm"])
    except (KeyError, ValueError, TypeError):
        return jsonify(ok=False, error="bpm must be a number")

    if not file_path:
        return jsonify(ok=False, error="file_path required")

    try:
        _db.lock_track(file_path, bpm)
        if _write_tags:
            write_bpm_tag(file_path, bpm)
        log.info("UI: locked %s at %.1f BPM", Path(file_path).name, bpm)
        return jsonify(ok=True)
    except Exception as exc:
        log.error("UI save_bpm error: %s", exc)
        return jsonify(ok=False, error=str(exc))


@app.route("/api/unlock", methods=["POST"])
@login_required
def api_unlock():
    data = request.get_json(force=True, silent=True) or {}
    file_path = data.get("file_path", "")
    if not file_path:
        return jsonify(ok=False, error="file_path required")
    try:
        _db.unlock_track(file_path)
        log.info("UI: unlocked %s", Path(file_path).name)
        return jsonify(ok=True)
    except Exception as exc:
        return jsonify(ok=False, error=str(exc))


# ---------------------------------------------------------------------------
# Health check (no auth — safe for Docker/k8s probes)
# ---------------------------------------------------------------------------

@app.route("/healthz")
def healthz():
    try:
        stats = _db.get_stats() if _db else {}
        return jsonify(status="ok", **stats)
    except Exception as exc:
        return jsonify(status="error", error=str(exc)), 500


# ---------------------------------------------------------------------------
# Audio streaming
# ---------------------------------------------------------------------------

@app.route("/audio")
@login_required
def audio():
    file_path = request.args.get("path", "")
    if not file_path:
        abort(400)
    real = os.path.realpath(file_path)
    music_real = os.path.realpath(_music_dir)
    # Prevent path traversal outside the music directory
    if not (real == music_real or real.startswith(music_real + os.sep)):
        abort(403)
    if not os.path.isfile(real):
        abort(404)
    return send_file(real, conditional=True)


# ---------------------------------------------------------------------------
# Entry point (called from bpm_tagger.main as a daemon thread)
# ---------------------------------------------------------------------------

def start(config: dict):
    global _db, _music_dir, _write_tags, _conf_threshold

    password = config.get("ui_password", "")
    if not password:
        log.error("UI: UI_PASSWORD is not set — web UI will not start")
        return

    global _max_login_attempts, _lockout_seconds
    from bpm_tagger import BPMDatabase
    _db = BPMDatabase(config["db_path"])
    _music_dir = config["music_dir"]
    _write_tags = config.get("write_tags", True)
    _conf_threshold = config.get("review_confidence_threshold", 0.4)
    _max_login_attempts = int(config.get("ui_max_login_attempts", 5))
    _lockout_seconds = int(config.get("ui_lockout_seconds", 300))

    secret = config.get("ui_secret_key") or secrets.token_hex(32)
    app.secret_key = secret
    app.config["UI_PASSWORD"] = password
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
        hours=int(config.get("ui_session_hours", 24))
    )
    app.config["SESSION_PERMANENT"] = True

    port = int(config.get("ui_port", 5000))
    log.info("BPM UI running on http://0.0.0.0:%d", port)
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
