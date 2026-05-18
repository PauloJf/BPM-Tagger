"""Flask web UI for BPM Tagger — runs as a daemon thread inside the main container."""

import hmac
import json
import logging
import os
import sys
import secrets
import threading
import time
import urllib.request
from collections import defaultdict
from datetime import timedelta
from functools import wraps
from pathlib import Path
from threading import Lock
from urllib.parse import urljoin, urlparse

from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                   request, send_file, session, url_for)

log = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates")

# Populated by start()
_db = None
_music_dir = ""
_write_tags = True
_conf_threshold = 0.4
_progress = None
_bpm_min = 60.0
_bpm_max = 200.0
_tagger = None
_config: dict = {}
_settings_path = ""
_restarting = False

# Brute-force login protection
_login_attempts: dict = defaultdict(list)
_login_lockout_until: dict = defaultdict(float)
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
# Security helpers
# ---------------------------------------------------------------------------

def _csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]

def _check_csrf():
    token = (request.form.get("csrf_token")
             or request.headers.get("X-CSRF-Token", ""))
    stored = session.get("csrf_token", "")
    if not token or not stored or not hmac.compare_digest(token, stored):
        abort(403)

def _is_safe_redirect(url: str) -> bool:
    ref = urlparse(request.host_url)
    test = urlparse(urljoin(request.host_url, url))
    return test.scheme in ("http", "https") and ref.netloc == test.netloc

def _assert_in_music_dir(file_path: str) -> str:
    real = os.path.realpath(file_path)
    music_real = os.path.realpath(_music_dir)
    if not (real == music_real or real.startswith(music_real + os.sep)):
        abort(403)
    return real

def _save_settings(updates: dict):
    existing = {}
    if os.path.isfile(_settings_path):
        try:
            with open(_settings_path) as f:
                existing = json.load(f)
        except Exception:
            pass
    existing.update(updates)
    with open(_settings_path, "w") as f:
        json.dump(existing, f, indent=2)


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

@app.after_request
def _security_headers(resp):
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; media-src 'self';"
    )
    return resp

@app.before_request
def _ensure_csrf():
    if request.endpoint not in (None, "static", "healthz", "login"):
        _csrf_token()


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
    _csrf_token()  # ensure token exists before rendering
    if request.method == "POST":
        _check_csrf()
        ip = request.remote_addr or "unknown"
        now = time.time()
        with _login_lock:
            if now < _login_lockout_until[ip]:
                return render_template("login.html", lockout=True), 429
            attempts = [t for t in _login_attempts[ip] if now - t < _attempt_window]
            _login_attempts[ip] = attempts
            if len(attempts) >= _max_login_attempts:
                _login_lockout_until[ip] = now + _lockout_seconds
                _login_attempts[ip] = []
                return render_template("login.html", lockout=True), 429
            if request.form.get("password") == app.config["UI_PASSWORD"]:
                _login_attempts.pop(ip, None)
                _login_lockout_until.pop(ip, None)
                session["ok"] = True
                next_url = request.args.get("next")
                target = next_url if next_url and _is_safe_redirect(next_url) else url_for("tracks")
                return redirect(target)
            _login_attempts[ip].append(now)
        flash("Wrong password.", "error")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    _check_csrf()
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
    rows, total = _db.get_tracks_page(q, per_page, (page - 1) * per_page)
    pages = max(1, (total + per_page - 1) // per_page)
    return render_template("tracks.html", tracks=rows, total=total, page=page, pages=pages,
                           q=q, per_page=per_page)


@app.route("/review")
@login_required
def review():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    per_page = 50

    total = _db.get_suspicious_count(_conf_threshold, _bpm_min, _bpm_max)
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    rows = _db.get_suspicious_page(_conf_threshold, _bpm_min, _bpm_max,
                                   per_page, (page - 1) * per_page)
    return render_template("review.html", tracks=rows, conf_threshold=_conf_threshold,
                           total=total, page=page, pages=pages)


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
        try:
            idx = queue.index(path)
            queue_pos = idx + 1
            prev_path = queue[idx - 1] if idx > 0 else None
            next_path = queue[idx + 1] if idx < len(queue) - 1 else None
        except ValueError:
            pass

    return render_template("track.html", track=track, back=back,
                           prev_path=prev_path, next_path=next_path,
                           queue_pos=queue_pos, queue_total=queue_total)


@app.route("/stats")
@login_required
def stats():
    return render_template("stats.html")


@app.route("/settings")
@login_required
def settings():
    from bpm_tagger import __version__
    return render_template("settings.html", cfg=_config, version=__version__)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.route("/api/save_bpm", methods=["POST"])
@login_required
def api_save_bpm():
    _check_csrf()
    from bpm_tagger import write_bpm_tag
    data = request.get_json(force=True, silent=True) or {}
    file_path = data.get("file_path", "")
    try:
        bpm = float(data["bpm"])
    except (KeyError, ValueError, TypeError):
        return jsonify(ok=False, error="bpm must be a number")

    if not file_path:
        return jsonify(ok=False, error="file_path required")

    _assert_in_music_dir(file_path)

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
    _check_csrf()
    data = request.get_json(force=True, silent=True) or {}
    file_path = data.get("file_path", "")
    if not file_path:
        return jsonify(ok=False, error="file_path required")

    _assert_in_music_dir(file_path)

    try:
        _db.unlock_track(file_path)
        log.info("UI: unlocked %s", Path(file_path).name)
        return jsonify(ok=True)
    except Exception as exc:
        return jsonify(ok=False, error=str(exc))


@app.route("/api/progress")
@login_required
def api_progress():
    if _progress is None:
        return jsonify(is_scanning=False, is_paused=False, is_stopping=False,
                       completed=0, total=0, cumulative_completed=0,
                       current_file="", current_step="",
                       step_index=0, step_total=3, last_file="", last_bpm=None)
    return jsonify(**_progress.snapshot())


@app.route("/api/tracks")
@login_required
def api_tracks():
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
    rows, total = _db.get_tracks_page(q, per_page, (page - 1) * per_page)
    pages = max(1, (total + per_page - 1) // per_page)
    return jsonify(tracks=rows, total=total, page=page, pages=pages, per_page=per_page)


@app.route("/api/stats")
@login_required
def api_stats():
    try:
        summary = _db.get_stats()
        return jsonify(
            summary=summary,
            bpm_distribution=_db.get_bpm_distribution(),
            detector_distribution=_db.get_detector_distribution(),
            bpm_descriptive=_db.get_bpm_descriptive(),
        )
    except Exception as exc:
        return jsonify(error=str(exc)), 500


# ---------------------------------------------------------------------------
# Scan control API
# ---------------------------------------------------------------------------

@app.route("/api/scan/start", methods=["POST"])
@login_required
def api_scan_start():
    _check_csrf()
    if _tagger is None:
        return jsonify(ok=False, error="tagger not available")
    if _progress and _progress.is_scanning:
        return jsonify(ok=False, error="scan already running")
    mode = _config.get("mode", "watch")
    if mode == "scan_review":
        target = _tagger.scan_review
    elif mode == "report":
        target = _tagger.report
    elif mode in ("scan_all", "watch_all"):
        target = lambda: _tagger.scan_directory(force=True)
    else:  # watch, scan_unscanned, default
        target = lambda: _tagger.scan_directory(force=False)
    threading.Thread(target=target, daemon=True).start()
    return jsonify(ok=True)


@app.route("/api/scan/pause", methods=["POST"])
@login_required
def api_scan_pause():
    _check_csrf()
    if _tagger is None:
        return jsonify(ok=False, error="tagger not available")
    _tagger._pause_event.clear()
    if _progress:
        _progress.set_paused(True)
    return jsonify(ok=True)


@app.route("/api/scan/resume", methods=["POST"])
@login_required
def api_scan_resume():
    _check_csrf()
    if _tagger is None:
        return jsonify(ok=False, error="tagger not available")
    _tagger._pause_event.set()
    if _progress:
        _progress.set_paused(False)
    return jsonify(ok=True)


@app.route("/api/scan/stop", methods=["POST"])
@login_required
def api_scan_stop():
    _check_csrf()
    if _tagger is None:
        return jsonify(ok=False, error="tagger not available")
    if _progress and _progress.is_scanning:
        _progress.set_stopping(True)
    _tagger._stop_event.set()
    _tagger._pause_event.set()  # unblock if currently paused
    return jsonify(ok=True)


@app.route("/api/restart", methods=["POST"])
@login_required
def api_restart():
    global _restarting
    _check_csrf()
    if _restarting:
        return jsonify(ok=True)
    _restarting = True
    if _tagger is not None and _progress and _progress.is_scanning:
        _progress.set_stopping(True)
        _tagger._stop_event.set()
        _tagger._pause_event.set()  # unblock if paused

    def _do_restart():
        time.sleep(1.5)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    threading.Thread(target=_do_restart, daemon=True).start()
    log.info("UI: restart requested — replacing process in 1.5 s")
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Settings save endpoints
# ---------------------------------------------------------------------------

@app.route("/settings/password", methods=["POST"])
@login_required
def settings_password():
    _check_csrf()
    current = request.form.get("current_password", "")
    new_pw  = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")
    if not hmac.compare_digest(current, app.config["UI_PASSWORD"]):
        flash("Current password is incorrect.", "error")
        return redirect(url_for("settings"))
    if not new_pw:
        flash("New password cannot be empty.", "error")
        return redirect(url_for("settings"))
    if new_pw != confirm:
        flash("New passwords do not match.", "error")
        return redirect(url_for("settings"))
    app.config["UI_PASSWORD"] = new_pw
    _config["ui_password"] = new_pw
    _save_settings({"ui_password": new_pw})
    flash("Password updated.", "success")
    return redirect(url_for("settings"))


@app.route("/settings/ntfy", methods=["POST"])
@login_required
def settings_ntfy():
    _check_csrf()
    global _config
    updates = {
        "ntfy_url":           request.form.get("ntfy_url", "").strip(),
        "ntfy_topic":         request.form.get("ntfy_topic", "").strip(),
        "ntfy_batch_size":    int(request.form.get("ntfy_batch_size", 10) or 10),
        "ntfy_min_interval":  int(request.form.get("ntfy_min_interval", 300) or 300),
        "ntfy_notify_review": request.form.get("ntfy_notify_review") == "on",
    }
    _config.update(updates)
    _save_settings(updates)
    # Rebuild notifier in-memory if tagger is available
    if _tagger is not None:
        from bpm_tagger import NotificationManager
        if updates["ntfy_url"] and updates["ntfy_topic"]:
            _tagger.notifier = NotificationManager(
                ntfy_url=updates["ntfy_url"],
                topic=updates["ntfy_topic"],
                batch_size=updates["ntfy_batch_size"],
                min_interval=updates["ntfy_min_interval"],
                notify_review=updates["ntfy_notify_review"],
            )
        else:
            _tagger.notifier = None
    flash("Notification settings saved.", "success")
    return redirect(url_for("settings"))


@app.route("/settings/scan", methods=["POST"])
@login_required
def settings_scan():
    _check_csrf()
    global _conf_threshold, _bpm_min, _bpm_max, _write_tags
    try:
        workers = max(1, min(8, int(request.form.get("workers", 1) or 1)))
    except (ValueError, TypeError):
        workers = 1
    try:
        conf_thr = float(request.form.get("review_confidence_threshold", 0.4) or 0.4)
        conf_thr = max(0.0, min(1.0, conf_thr))
    except (ValueError, TypeError):
        conf_thr = 0.4
    try:
        bpm_min = float(request.form.get("bpm_min", 60.0) or 60.0)
        bpm_max = float(request.form.get("bpm_max", 200.0) or 200.0)
    except (ValueError, TypeError):
        bpm_min, bpm_max = 60.0, 200.0

    updates = {
        "workers":                    workers,
        "use_deeprhythm":             request.form.get("use_deeprhythm") == "on",
        "use_essentia":               request.form.get("use_essentia") == "on",
        "write_tags":                 request.form.get("write_tags") == "on",
        "review_confidence_threshold": conf_thr,
        "bpm_min":                    bpm_min,
        "bpm_max":                    bpm_max,
    }
    _config.update(updates)
    if _tagger is not None:
        _tagger.config.update(updates)
    _conf_threshold = conf_thr
    _bpm_min = bpm_min
    _bpm_max = bpm_max
    _write_tags = updates["write_tags"]
    _save_settings(updates)
    flash("Scan settings saved.", "success")
    return redirect(url_for("settings"))


@app.route("/settings/mode", methods=["POST"])
@login_required
def settings_mode():
    _check_csrf()
    valid_modes = ("watch", "watch_all", "scan_all", "scan_unscanned", "scan_review", "report")
    mode = request.form.get("mode", "watch")
    if mode not in valid_modes:
        flash("Invalid mode.", "error")
        return redirect(url_for("settings"))
    _config["mode"] = mode
    _save_settings({"mode": mode})
    flash(f"Mode set to '{mode}'. Restart required to take effect.", "success")
    return redirect(url_for("settings"))


@app.route("/settings/navidrome", methods=["POST"])
@login_required
def settings_navidrome():
    _check_csrf()
    updates = {
        "navidrome_url":  request.form.get("navidrome_url", "").strip(),
        "navidrome_user": request.form.get("navidrome_user", "").strip(),
        "navidrome_pass": request.form.get("navidrome_pass", "").strip(),
    }
    _config.update(updates)
    if _tagger is not None:
        _tagger.config.update(updates)
    _save_settings(updates)
    flash("Navidrome settings saved.", "success")
    return redirect(url_for("settings"))


# ---------------------------------------------------------------------------
# Version check
# ---------------------------------------------------------------------------

@app.route("/api/version/check")
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
    except Exception as exc:
        return jsonify(error=str(exc))


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
    real = _assert_in_music_dir(file_path)
    if not os.path.isfile(real):
        abort(404)
    return send_file(real, conditional=True)


# ---------------------------------------------------------------------------
# Entry point (called from bpm_tagger.main as a daemon thread)
# ---------------------------------------------------------------------------

def start(config: dict, progress=None, tagger=None):
    global _db, _music_dir, _write_tags, _conf_threshold, _progress, _bpm_min, _bpm_max
    global _max_login_attempts, _lockout_seconds, _tagger, _config, _settings_path

    password = config.get("ui_password", "")
    if not password:
        log.error("UI: UI_PASSWORD is not set — web UI will not start")
        return

    from bpm_tagger import BPMDatabase
    _db = BPMDatabase(config["db_path"])
    _music_dir = config["music_dir"]
    _write_tags = config.get("write_tags", True)
    _conf_threshold = config.get("review_confidence_threshold", 0.4)
    _progress = progress
    _bpm_min = float(config.get("bpm_min", 60.0))
    _bpm_max = float(config.get("bpm_max", 200.0))
    _max_login_attempts = int(config.get("ui_max_login_attempts", 5))
    _lockout_seconds = int(config.get("ui_lockout_seconds", 300))
    _tagger = tagger
    _config = config
    _settings_path = str(Path(config["db_path"]).parent / "settings.json")

    secret = config.get("ui_secret_key") or secrets.token_hex(32)
    app.secret_key = secret
    app.config["UI_PASSWORD"] = password
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
        hours=int(config.get("ui_session_hours", 24))
    )
    app.config["SESSION_PERMANENT"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_HTTPONLY"] = True

    from waitress import serve
    port = int(config.get("ui_port", 5000))
    log.info("BPM UI running on http://0.0.0.0:%d", port)
    serve(app, host="0.0.0.0", port=port, threads=8)
