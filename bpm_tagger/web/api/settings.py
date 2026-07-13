"""Settings endpoints for the SPA: GET merged config + JSON POST per section.

Persist to settings.json and update live AppState. GET masks secrets.
"""

import hmac
import logging

import requests
from flask import Blueprint, current_app, jsonify, request

from ...config import __version__, env_locked_keys, save_settings
from ...integrations.navidrome import ping_navidrome
from ...notify.ntfy import NotificationManager
from ..auth import _check_csrf, login_required
from ..state import state

log = logging.getLogger(__name__)

settings_bp = Blueprint("settings", __name__)

# Values that must never be returned to the client in the clear.
_SECRET_KEYS = {"ui_password", "ui_secret_key", "navidrome_pass",
                "spotify_client_secret", "monochrome_api_key", "deezer_arl"}


def _json_body() -> dict:
    return request.get_json(force=True, silent=True) or {}


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
    return jsonify(settings=out, version=__version__, env_locked=env_locked_keys())


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
        "preserve_mtime":              bool(data.get("preserve_mtime", True)),
        "review_confidence_threshold": conf_thr,
        "bpm_min":                     bpm_min,
        "bpm_max":                     bpm_max,
    }
    # Env-locked settings are controlled by docker-compose — ignore client edits
    # so they are neither applied nor persisted to settings.json.
    for key in env_locked_keys():
        updates.pop(key, None)
    st.config.update(updates)
    if st.tagger is not None:
        st.tagger.config.update(updates)
    st.conf_threshold = conf_thr
    st.bpm_min = bpm_min
    st.bpm_max = bpm_max
    st.write_tags = updates["write_tags"]
    st.preserve_mtime = st.config.get("preserve_mtime", True)
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


@settings_bp.route("/api/settings/run", methods=["POST"])
@login_required
def api_settings_run():
    _check_csrf()
    st = state()
    data = _json_body()

    def _num(v, lo, hi, dflt):
        try:
            return max(lo, min(hi, float(v)))
        except (ValueError, TypeError):
            return dflt

    from ...config import RUN_PRESET_DEFAULTS
    presets = []
    raw = data.get("run_presets", [])
    if isinstance(raw, list):
        for p in raw[:4]:
            dflt_name, dflt_bpm = RUN_PRESET_DEFAULTS[len(presets)]
            if isinstance(p, dict):
                name = str(p.get("name", "")).strip()[:20] or dflt_name
                bpm = int(_num(p.get("bpm"), 30, 300, dflt_bpm))
            else:  # legacy shape: plain number
                name, bpm = dflt_name, int(_num(p, 30, 300, dflt_bpm))
            presets.append({"name": name, "bpm": bpm})
    presets += [{"name": n, "bpm": b} for n, b in RUN_PRESET_DEFAULTS[len(presets):]]

    updates = {
        "run_presets":           presets,
        "run_octave_fold":       bool(data.get("run_octave_fold", True)),
        "run_prefer_starred":    bool(data.get("run_prefer_starred", True)),
        "run_queue_size":        int(_num(data.get("run_queue_size"), 1, 200, 20)),
        "run_tolerance_pct":     _num(data.get("run_tolerance_pct"), 0.5, 30, 4.0),
        "run_stretch_limit_pct": _num(data.get("run_stretch_limit_pct"), 1, 50, 15.0),
    }
    st.config.update(updates)
    save_settings(st.settings_path, updates)
    return jsonify(ok=True)


@settings_bp.route("/api/settings/artwork", methods=["POST"])
@login_required
def api_settings_artwork():
    _check_csrf()
    st = state()
    data = _json_body()
    updates = {
        "fetch_artist_images":      bool(data.get("fetch_artist_images")),
        "artist_images_to_library": bool(data.get("artist_images_to_library")),
    }
    st.config.update(updates)
    save_settings(st.settings_path, updates)
    return jsonify(ok=True)


@settings_bp.route("/api/settings/lyrics", methods=["POST"])
@login_required
def api_settings_lyrics():
    _check_csrf()
    st = state()
    data = _json_body()
    mode = str(data.get("lyrics_mode", "embed")).strip()
    if mode not in ("embed", "sidecar"):
        mode = "embed"
    updates = {
        "lyrics_enabled": bool(data.get("lyrics_enabled")),
        "lyrics_mode":    mode,
    }
    st.config.update(updates)
    if st.tagger is not None:
        st.tagger.config.update(updates)
    save_settings(st.settings_path, updates)
    return jsonify(ok=True)


@settings_bp.route("/api/settings/grabber", methods=["POST"])
@login_required
def api_settings_grabber():
    _check_csrf()
    st = state()
    data = _json_body()
    updates = {}
    if "grabber_enabled" in data:
        updates["grabber_enabled"] = bool(data["grabber_enabled"])
    if "index_tags" in data:
        updates["index_tags"] = bool(data["index_tags"])
    if "grab_dry_run" in data:
        updates["grab_dry_run"] = bool(data["grab_dry_run"])
    if "ui_public_url" in data:
        updates["ui_public_url"] = str(data["ui_public_url"]).strip()
    if "spotify_sync_minutes" in data:
        try:
            updates["spotify_sync_minutes"] = max(1, int(data["spotify_sync_minutes"]))
        except (ValueError, TypeError):
            pass
    # Provider / output / path options (env defaults; UI can override at runtime).
    for key in ("output_format", "path_template", "provider_order",
                "monochrome_base_url", "monochrome_quality", "deezer_quality"):
        if key in data:
            updates[key] = str(data[key]).strip()
    # Monochrome API key: only overwrite when a non-masked value is supplied.
    mk = str(data.get("monochrome_api_key", ""))
    if mk and mk != "********":
        updates["monochrome_api_key"] = mk.strip()
    # Deezer ARL (secret): only overwrite when a non-masked value is supplied.
    arl = str(data.get("deezer_arl", ""))
    if arl and arl != "********":
        updates["deezer_arl"] = arl.strip()
    st.config.update(updates)
    # Live-apply provider changes to the running grabber, if any.
    g = getattr(st.tagger, "grabber", None) if st.tagger else None
    if g is not None and any(k in updates for k in
                             ("provider_order", "output_format", "path_template",
                              "monochrome_base_url", "monochrome_api_key", "monochrome_quality",
                              "deezer_arl", "deezer_quality")):
        try:
            from ...grabber.providers import build_providers
            g.pool.pipeline.providers = build_providers(st.config)
            g.pool.pipeline.output_format = st.config.get("output_format", "mp3-320")
            g.pool.pipeline.path_template = st.config.get("path_template", g.pool.pipeline.path_template)
        except Exception as exc:
            log.warning("Could not live-apply provider settings: %s", exc)
    save_settings(st.settings_path, updates)
    # Toggling grabber_enabled needs a restart (threads/service wire up at boot).
    return jsonify(ok=True, restart_required="grabber_enabled" in updates)


@settings_bp.route("/api/settings/test-ntfy", methods=["POST"])
@login_required
def api_test_ntfy():
    _check_csrf()
    data = _json_body()
    url = str(data.get("ntfy_url", "")).strip().rstrip("/")
    topic = str(data.get("ntfy_topic", "")).strip()
    if not (url and topic):
        return jsonify(ok=False, error="URL and topic required"), 400
    try:
        resp = requests.post(f"{url}/{topic}", data=b"BPM Tagger test notification",
                             headers={"Title": "BPM Tagger", "Tags": "white_check_mark"}, timeout=10)
        resp.raise_for_status()
        return jsonify(ok=True, message="Test notification sent")
    except Exception as exc:
        return jsonify(ok=False, error=str(exc))


@settings_bp.route("/api/settings/test-navidrome", methods=["POST"])
@login_required
def api_test_navidrome():
    _check_csrf()
    data = _json_body()
    ok, msg = ping_navidrome(str(data.get("navidrome_url", "")).strip(),
                             str(data.get("navidrome_user", "")).strip(),
                             str(data.get("navidrome_pass", "")))
    return jsonify(ok=ok, message=msg if ok else None, error=None if ok else msg)


@settings_bp.route("/api/settings/test-monochrome", methods=["POST"])
@login_required
def api_test_monochrome():
    _check_csrf()
    data = _json_body()
    base = str(data.get("monochrome_base_url", "")).strip()
    if not base:
        return jsonify(ok=False, error="Base URL required"), 400
    key = str(data.get("monochrome_api_key", ""))
    if key == "********":
        key = state().config.get("monochrome_api_key", "")
    from ...grabber.providers.monochrome import MonochromeProvider
    ok = MonochromeProvider({"monochrome_base_url": base, "monochrome_api_key": key}).healthcheck()
    return jsonify(ok=ok, message="Reachable" if ok else None,
                   error=None if ok else "Health check failed")


@settings_bp.route("/api/settings/test-deezer", methods=["POST"])
@login_required
def api_test_deezer():
    _check_csrf()
    data = _json_body()
    arl = str(data.get("deezer_arl", ""))
    if arl == "********" or not arl:
        arl = state().config.get("deezer_arl", "")
    if not arl:
        return jsonify(ok=False, error="ARL required"), 400
    from ...grabber.providers.deezer import DeezerProvider
    ok = DeezerProvider({"deezer_arl": arl.strip()}).healthcheck()
    return jsonify(ok=ok, message="ARL accepted" if ok else None,
                   error=None if ok else "Login failed (ARL invalid or expired)")


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
