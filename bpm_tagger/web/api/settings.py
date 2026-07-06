"""Settings-save endpoints (form POSTs). Persist to settings.json and update live state."""

import hmac
import logging

from flask import (Blueprint, current_app, flash, jsonify, redirect, request,
                   url_for)

from ...config import __version__, save_settings
from ...notify.ntfy import NotificationManager
from ..auth import _check_csrf, login_required
from ..state import state

log = logging.getLogger(__name__)

settings_bp = Blueprint("settings", __name__)

# Values that must never be returned to the client in the clear.
_SECRET_KEYS = {"ui_password", "ui_secret_key", "navidrome_pass"}


def _json_body() -> dict:
    return request.get_json(force=True, silent=True) or {}


@settings_bp.route("/settings/password", methods=["POST"])
@login_required
def settings_password():
    _check_csrf()
    st = state()
    current = request.form.get("current_password", "")
    new_pw  = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")
    if not hmac.compare_digest(current, current_app.config["UI_PASSWORD"]):
        flash("Current password is incorrect.", "error")
        return redirect(url_for("pages.settings"))
    if not new_pw:
        flash("New password cannot be empty.", "error")
        return redirect(url_for("pages.settings"))
    if new_pw != confirm:
        flash("New passwords do not match.", "error")
        return redirect(url_for("pages.settings"))
    current_app.config["UI_PASSWORD"] = new_pw
    st.config["ui_password"] = new_pw
    save_settings(st.settings_path, {"ui_password": new_pw})
    flash("Password updated.", "success")
    return redirect(url_for("pages.settings"))


@settings_bp.route("/settings/ntfy", methods=["POST"])
@login_required
def settings_ntfy():
    _check_csrf()
    st = state()
    updates = {
        "ntfy_url":           request.form.get("ntfy_url", "").strip(),
        "ntfy_topic":         request.form.get("ntfy_topic", "").strip(),
        "ntfy_batch_size":    int(request.form.get("ntfy_batch_size", 10) or 10),
        "ntfy_min_interval":  int(request.form.get("ntfy_min_interval", 300) or 300),
        "ntfy_notify_review": request.form.get("ntfy_notify_review") == "on",
    }
    st.config.update(updates)
    save_settings(st.settings_path, updates)
    # Rebuild notifier in-memory if tagger is available
    if st.tagger is not None:
        if updates["ntfy_url"] and updates["ntfy_topic"]:
            st.tagger.notifier = NotificationManager(
                ntfy_url=updates["ntfy_url"],
                topic=updates["ntfy_topic"],
                batch_size=updates["ntfy_batch_size"],
                min_interval=updates["ntfy_min_interval"],
                notify_review=updates["ntfy_notify_review"],
            )
        else:
            st.tagger.notifier = None
    flash("Notification settings saved.", "success")
    return redirect(url_for("pages.settings"))


@settings_bp.route("/settings/scan", methods=["POST"])
@login_required
def settings_scan():
    _check_csrf()
    st = state()
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
    st.config.update(updates)
    if st.tagger is not None:
        st.tagger.config.update(updates)
    st.conf_threshold = conf_thr
    st.bpm_min = bpm_min
    st.bpm_max = bpm_max
    st.write_tags = updates["write_tags"]
    save_settings(st.settings_path, updates)
    flash("Scan settings saved.", "success")
    return redirect(url_for("pages.settings"))


@settings_bp.route("/settings/mode", methods=["POST"])
@login_required
def settings_mode():
    _check_csrf()
    st = state()
    valid_modes = ("watch", "watch_all", "scan_all", "scan_unscanned", "scan_review", "report")
    mode = request.form.get("mode", "watch")
    if mode not in valid_modes:
        flash("Invalid mode.", "error")
        return redirect(url_for("pages.settings"))
    st.config["mode"] = mode
    save_settings(st.settings_path, {"mode": mode})
    flash(f"Mode set to '{mode}'. Restart required to take effect.", "success")
    return redirect(url_for("pages.settings"))


@settings_bp.route("/settings/navidrome", methods=["POST"])
@login_required
def settings_navidrome():
    _check_csrf()
    st = state()
    updates = {
        "navidrome_url":  request.form.get("navidrome_url", "").strip(),
        "navidrome_user": request.form.get("navidrome_user", "").strip(),
        "navidrome_pass": request.form.get("navidrome_pass", "").strip(),
    }
    st.config.update(updates)
    if st.tagger is not None:
        st.tagger.config.update(updates)
    save_settings(st.settings_path, updates)
    flash("Navidrome settings saved.", "success")
    return redirect(url_for("pages.settings"))


@settings_bp.route("/settings/playback", methods=["POST"])
@login_required
def settings_playback():
    _check_csrf()
    st = state()
    try:
        buf = float(request.form.get("playback_buffer", 3) or 3)
        buf = max(0.0, min(30.0, buf))
    except (ValueError, TypeError):
        buf = 3.0
    updates = {"playback_buffer": buf}
    st.config.update(updates)
    save_settings(st.settings_path, updates)
    flash("Playback settings saved.", "success")
    return redirect(url_for("pages.settings"))


# ---------------------------------------------------------------------------
# JSON API for the React SPA
#
# GET returns the merged env + settings.json config with secrets masked; the
# POST handlers below mirror the form handlers above but accept JSON bodies and
# return JSON instead of flash + redirect. The form routes stay for the M0
# Jinja UI and are removed in M2.
# ---------------------------------------------------------------------------

@settings_bp.route("/api/settings")
@login_required
def api_settings_get():
    st = state()
    cfg = dict(st.config)
    out = {}
    for key, val in cfg.items():
        if key in _SECRET_KEYS:
            out[key] = "********" if val else ""
        elif isinstance(val, (set, frozenset)):
            out[key] = sorted(val)
        else:
            out[key] = val
    return jsonify(settings=out, version=__version__)


@settings_bp.route("/api/settings/ntfy", methods=["POST"])
@login_required
def api_settings_ntfy():
    _check_csrf()
    st = state()
    data = _json_body()
    updates = {
        "ntfy_url":           str(data.get("ntfy_url", "")).strip(),
        "ntfy_topic":         str(data.get("ntfy_topic", "")).strip(),
        "ntfy_batch_size":    int(data.get("ntfy_batch_size", 10) or 10),
        "ntfy_min_interval":  int(data.get("ntfy_min_interval", 300) or 300),
        "ntfy_notify_review": bool(data.get("ntfy_notify_review")),
    }
    st.config.update(updates)
    save_settings(st.settings_path, updates)
    if st.tagger is not None:
        if updates["ntfy_url"] and updates["ntfy_topic"]:
            st.tagger.notifier = NotificationManager(
                ntfy_url=updates["ntfy_url"],
                topic=updates["ntfy_topic"],
                batch_size=updates["ntfy_batch_size"],
                min_interval=updates["ntfy_min_interval"],
                notify_review=updates["ntfy_notify_review"],
            )
        else:
            st.tagger.notifier = None
    return jsonify(ok=True)


@settings_bp.route("/api/settings/scan", methods=["POST"])
@login_required
def api_settings_scan():
    _check_csrf()
    st = state()
    data = _json_body()
    try:
        workers = max(1, min(8, int(data.get("workers", 1) or 1)))
    except (ValueError, TypeError):
        workers = 1
    try:
        conf_thr = max(0.0, min(1.0, float(data.get("review_confidence_threshold", 0.4) or 0.4)))
    except (ValueError, TypeError):
        conf_thr = 0.4
    try:
        bpm_min = float(data.get("bpm_min", 60.0) or 60.0)
        bpm_max = float(data.get("bpm_max", 200.0) or 200.0)
    except (ValueError, TypeError):
        bpm_min, bpm_max = 60.0, 200.0

    updates = {
        "workers":                     workers,
        "use_deeprhythm":              bool(data.get("use_deeprhythm")),
        "use_essentia":                bool(data.get("use_essentia")),
        "write_tags":                  bool(data.get("write_tags")),
        "review_confidence_threshold": conf_thr,
        "bpm_min":                     bpm_min,
        "bpm_max":                     bpm_max,
    }
    st.config.update(updates)
    if st.tagger is not None:
        st.tagger.config.update(updates)
    st.conf_threshold = conf_thr
    st.bpm_min = bpm_min
    st.bpm_max = bpm_max
    st.write_tags = updates["write_tags"]
    save_settings(st.settings_path, updates)
    return jsonify(ok=True)


@settings_bp.route("/api/settings/mode", methods=["POST"])
@login_required
def api_settings_mode():
    _check_csrf()
    st = state()
    valid_modes = ("watch", "watch_all", "scan_all", "scan_unscanned", "scan_review", "report")
    mode = _json_body().get("mode", "watch")
    if mode not in valid_modes:
        return jsonify(ok=False, error="Invalid mode."), 400
    st.config["mode"] = mode
    save_settings(st.settings_path, {"mode": mode})
    return jsonify(ok=True, restart_required=True)


@settings_bp.route("/api/settings/navidrome", methods=["POST"])
@login_required
def api_settings_navidrome():
    _check_csrf()
    st = state()
    data = _json_body()
    updates = {
        "navidrome_url":  str(data.get("navidrome_url", "")).strip(),
        "navidrome_user": str(data.get("navidrome_user", "")).strip(),
    }
    # Only overwrite the stored password when a non-masked value is supplied.
    pw = str(data.get("navidrome_pass", ""))
    if pw and pw != "********":
        updates["navidrome_pass"] = pw.strip()
    st.config.update(updates)
    if st.tagger is not None:
        st.tagger.config.update(updates)
    save_settings(st.settings_path, updates)
    return jsonify(ok=True)


@settings_bp.route("/api/settings/playback", methods=["POST"])
@login_required
def api_settings_playback():
    _check_csrf()
    st = state()
    try:
        buf = max(0.0, min(30.0, float(_json_body().get("playback_buffer", 3) or 3)))
    except (ValueError, TypeError):
        buf = 3.0
    updates = {"playback_buffer": buf}
    st.config.update(updates)
    save_settings(st.settings_path, updates)
    return jsonify(ok=True)


@settings_bp.route("/api/settings/password", methods=["POST"])
@login_required
def api_settings_password():
    _check_csrf()
    st = state()
    data = _json_body()
    current = str(data.get("current_password", ""))
    new_pw  = str(data.get("new_password", ""))
    confirm = str(data.get("confirm_password", ""))
    if not hmac.compare_digest(current, current_app.config["UI_PASSWORD"]):
        return jsonify(ok=False, error="Current password is incorrect."), 400
    if not new_pw:
        return jsonify(ok=False, error="New password cannot be empty."), 400
    if new_pw != confirm:
        return jsonify(ok=False, error="New passwords do not match."), 400
    current_app.config["UI_PASSWORD"] = new_pw
    st.config["ui_password"] = new_pw
    save_settings(st.settings_path, {"ui_password": new_pw})
    return jsonify(ok=True)
