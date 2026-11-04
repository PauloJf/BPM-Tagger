"""Music Grabber link: request missing tracks from a separate Music Grabber.

Inert until an admin sets a URL and an API key (Settings → Integrations, or
``MUSIC_GRABBER_URL`` / ``MUSIC_GRABBER_API_KEY``). Requests go server to server;
the browser only ever sees request states and the public link.

Who may request: the admin, and members unless ``music_grabber_members_request``
is off. Player (kiosk) sessions never reach these routes (default-deny list).
"""

from flask import Blueprint, jsonify, request, session

from ...config import save_settings
from ..auth import _check_csrf, admin_username, login_required
from ..state import state

grabber_bp = Blueprint("api_grabber", __name__)


def _mg():
    from ...integrations import music_grabber
    return music_grabber


def _can_request(cfg: dict) -> bool:
    role = session.get("role")
    if role == "admin":
        return True
    return role == "member" and bool(cfg.get("music_grabber_members_request", True))


def _who() -> str:
    """The account name sent as ``requested_for`` (shown in Music Grabber's queue)."""
    if session.get("role") == "admin":
        return admin_username() or "admin"
    pid = session.get("player_id")
    row = state().db.get_player(pid) if pid is not None else None
    return row["username"] if row else "member"


def _err(e):
    return jsonify(error=e.code, message=str(e)), e.http


@grabber_bp.route("/api/grabber/status")
@login_required
def grabber_status():
    cfg = state().config
    mg = _mg()
    on = mg.configured(cfg)
    return jsonify(configured=on, public_url=mg.public_url(cfg) if on else "",
                   can_request=on and _can_request(cfg))


@grabber_bp.route("/api/grabber/settings", methods=["POST"])
@login_required
def grabber_settings():
    if session.get("role") != "admin":
        return jsonify(error="forbidden"), 403
    _check_csrf()
    st = state()
    mg = _mg()
    data = request.get_json(force=True, silent=True) or {}
    url = str(data.get("music_grabber_url", "") or "").strip()
    pub = str(data.get("music_grabber_public_url", "") or "").strip()
    if url and not mg.clean_url(url):
        return jsonify(error="bad_url", message="The address must start with http:// or https://"), 400
    if pub and not mg.clean_url(pub):
        return jsonify(error="bad_url", message="The public address must start with http:// or https://"), 400
    updates = {"music_grabber_url": mg.clean_url(url), "music_grabber_public_url": mg.clean_url(pub),
               "music_grabber_members_request": bool(data.get("music_grabber_members_request", True))}
    key = str(data.get("music_grabber_api_key", "") or "").strip()
    if key and key != "********":
        updates["music_grabber_api_key"] = key
    elif data.get("music_grabber_api_key") == "" and data.get("clear_key"):
        updates["music_grabber_api_key"] = ""
    st.config.update(updates)
    if st.tagger is not None:
        st.tagger.config.update(updates)
    save_settings(st.settings_path, updates)
    return jsonify(ok=True, configured=mg.configured(st.config))


@grabber_bp.route("/api/grabber/test", methods=["POST"])
@login_required
def grabber_test():
    if session.get("role") != "admin":
        return jsonify(error="forbidden"), 403
    _check_csrf()
    mg = _mg()
    try:
        return jsonify(mg.Client(state().config).check())
    except mg.GrabberError as e:
        return _err(e)


@grabber_bp.route("/api/grabber/request", methods=["POST"])
@login_required
def grabber_request():
    """Request tracks. Body: ``{"playlist_track_ids": [..]}`` (a playlist's missing
    rows) or ``{"dz_id": "..", "title": "..", "artist": ".."}`` (a catalogue track).
    Answers per-ref states; one failure doesn't stop the rest."""
    _check_csrf()
    st = state()
    mg = _mg()
    if not mg.configured(st.config):
        return jsonify(error="not_configured", message="Music Grabber isn't set up"), 400
    if not _can_request(st.config):
        return jsonify(error="forbidden"), 403
    data = request.get_json(force=True, silent=True) or {}
    who = _who()
    jobs = []                    # (ref, payload, title, artist, playlist_track_id)
    raw_ids = data.get("playlist_track_ids")
    if isinstance(raw_ids, list) and raw_ids:
        ids = []
        for x in raw_ids:
            try:
                ids.append(int(x))
            except (TypeError, ValueError):
                continue
        ids = list(dict.fromkeys(ids))
        if len(ids) > mg.MAX_BATCH:
            return jsonify(error="too_many", message=f"request at most {mg.MAX_BATCH} tracks at once"), 400
        with st.db._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM playlist_tracks WHERE id IN ({','.join('?' * len(ids))}) "
                "AND removed_at IS NULL", ids).fetchall() if ids else []
        for r in rows:
            pt = dict(r)
            if not (pt.get("title") and pt.get("artist")) and not pt.get("isrc"):
                continue
            jobs.append((f"pt:{pt['id']}", mg.payload_for_playlist_track(pt), pt.get("title"),
                         pt.get("artist"), pt["id"]))
    elif data.get("dz_id"):
        dz = str(data["dz_id"]).strip()
        if not dz.isdigit():
            return jsonify(error="bad_id"), 400
        jobs.append((f"dz:{dz}", {"dz_id": dz}, str(data.get("title") or ""), str(data.get("artist") or ""), None))
    if not jobs:
        return jsonify(error="nothing_to_request"), 400
    out, errors = {}, {}
    client = mg.Client(st.config)
    for ref, payload, title, artist, pt_id in jobs:
        try:
            out[ref] = mg.request_track(st.db, st.config, ref, payload, who=who, title=title or "",
                                        artist=artist or "", playlist_track_id=pt_id, client=client)
        except mg.GrabberError as e:
            errors[ref] = {"error": e.code, "message": str(e)}
            if e.code in ("unreachable", "bad_key", "missing_scope"):
                break            # the rest would fail the same way
    status = 200 if out else 502
    return jsonify(requests=out, errors=errors), status


@grabber_bp.route("/api/grabber/requests")
@login_required
def grabber_requests():
    """States for ``refs=pt:1,dz:2,...`` (at most 500), refreshed from Music Grabber
    when some are still in flight."""
    st = state()
    mg = _mg()
    refs = [r.strip() for r in str(request.args.get("refs", "")).split(",") if r.strip()][:500]
    if not refs:
        return jsonify(requests={})
    return jsonify(requests=mg.refresh(st.db, st.config, refs))
