"""Lyrics endpoints: per-track get/fetch/save + a bulk LRCLIB fill job.

Embedded writes change the file size, so every write refreshes the DB hash
(watcher anti-loop), mirroring the cover-edit endpoints.
"""

import logging
import threading
import time
from pathlib import Path

from flask import Blueprint, abort, jsonify, request

from ...bpm.lyrics import is_synced, read_lyrics, write_lyrics
from ...bpm.tags import get_file_hash
from ...integrations.lrclib import fetch_lyrics
from ..auth import _check_csrf, login_required
from ..state import _assert_in_music_dir, state

log = logging.getLogger(__name__)

lyrics_bp = Blueprint("api_lyrics", __name__)


def _lyrics_result(text: str | None, source: str, status: str | None) -> dict:
    return {"lyrics": text or "", "synced": is_synced(text), "source": source,
            "status": status or ""}


@lyrics_bp.route("/api/track/lyrics")
@login_required
def api_track_lyrics_get():
    """Current lyrics for a track (embedded tag or .lrc sidecar)."""
    st = state()
    path = request.args.get("path", "")
    if not path:
        abort(400)
    _assert_in_music_dir(path)
    track = st.db.get_track(path)
    if not track:
        abort(404)
    found = read_lyrics(path)
    if found:
        text, source = found
        return jsonify(**_lyrics_result(text, source, track.get("lyrics_status")))
    return jsonify(**_lyrics_result(None, "none", track.get("lyrics_status")))


def _apply_fetched(st, path: str, result: dict) -> dict:
    """Write an LRCLIB result onto a track + record its DB state. Returns the
    JSON payload for the response (also used per-item by the bulk fill)."""
    if result.get("instrumental"):
        st.db.set_lyrics_state(path, "instrumental", False)
        return {"ok": True, "status": "instrumental", "lyrics": "", "synced": False}
    text = result.get("synced") or result.get("plain") or ""
    mode = st.config.get("lyrics_mode", "embed")
    if not write_lyrics(path, text, mode=mode, preserve_mtime=st.preserve_mtime):
        return {"ok": False, "error": "lyrics write failed"}
    st.db.refresh_track_hash(path, get_file_hash(path))
    st.db.set_lyrics_state(path, "fetched", is_synced(text))
    return {"ok": True, "status": "fetched", "lyrics": text, "synced": is_synced(text)}


@lyrics_bp.route("/api/track/lyrics/fetch", methods=["POST"])
@login_required
def api_track_lyrics_fetch():
    """Fetch lyrics for one track from LRCLIB and write them (mode from settings)."""
    _check_csrf()
    st = state()
    path = (request.get_json(force=True, silent=True) or {}).get("file_path", "")
    if not path:
        return jsonify(ok=False, error="file_path required"), 400
    _assert_in_music_dir(path)
    track = st.db.get_track(path)
    if not track:
        return jsonify(ok=False, error="not found"), 404
    if not (track.get("artist") and track.get("title")):
        return jsonify(ok=False, error="Track needs artist + title tags to look up lyrics.")
    result = fetch_lyrics(track["artist"], track["title"], track.get("album") or "",
                          track.get("duration_ms"))
    if not result:
        st.db.set_lyrics_state(path, "not_found", False)
        return jsonify(ok=False, error="No lyrics found on LRCLIB.", status="not_found")
    payload = _apply_fetched(st, path, result)
    code = 200 if payload.get("ok") else 500
    return jsonify(**payload), code


@lyrics_bp.route("/api/track/lyrics", methods=["PUT"])
@login_required
def api_track_lyrics_put():
    """Manually save (or, with empty text, remove) a track's lyrics."""
    _check_csrf()
    st = state()
    data = request.get_json(force=True, silent=True) or {}
    path = data.get("file_path", "")
    if not path:
        return jsonify(ok=False, error="file_path required"), 400
    _assert_in_music_dir(path)
    if not st.db.get_track(path):
        return jsonify(ok=False, error="not found"), 404
    text = str(data.get("lyrics") or "").strip()
    mode = st.config.get("lyrics_mode", "embed")
    if not write_lyrics(path, text, mode=mode, preserve_mtime=st.preserve_mtime):
        return jsonify(ok=False, error="lyrics write failed"), 500
    st.db.refresh_track_hash(path, get_file_hash(path))
    if text:
        st.db.set_lyrics_state(path, "embedded", is_synced(text))
    else:
        st.db.set_lyrics_state(path, None, False)
    log.info("UI: saved lyrics for %s", Path(path).name)
    return jsonify(ok=True, synced=is_synced(text))


# ── Bulk lyrics fill (background job, mirrors the ISRC fill) ─────────────────
_fill = {"running": False, "total": 0, "done": 0, "filled": 0,
         "not_found": 0, "cancel": False}
_fill_lock = threading.Lock()


def _run_lyrics_fill(st, retry_not_found: bool):
    try:
        tracks = st.db.get_tracks_missing_lyrics(limit=5000, retry_not_found=retry_not_found)
        with _fill_lock:
            _fill.update(running=True, total=len(tracks), done=0, filled=0,
                         not_found=0, cancel=False)
        for t in tracks:
            with _fill_lock:
                if _fill["cancel"]:
                    break
            path = t["file_path"]
            try:
                # Already has lyrics on disk (pre-existing tag/sidecar)? Just index it.
                found = read_lyrics(path)
                if found:
                    st.db.set_lyrics_state(path, "embedded", is_synced(found[0]))
                    with _fill_lock:
                        _fill["filled"] += 1
                    continue
                result = fetch_lyrics(t.get("artist") or "", t.get("title") or "",
                                      t.get("album") or "", t.get("duration_ms"))
                if result:
                    payload = _apply_fetched(st, path, result)
                    with _fill_lock:
                        _fill["filled" if payload.get("ok") else "not_found"] += 1
                else:
                    st.db.set_lyrics_state(path, "not_found", False)
                    with _fill_lock:
                        _fill["not_found"] += 1
            except Exception as exc:
                log.warning("Lyrics fill failed for %s: %s", path, exc)
                with _fill_lock:
                    _fill["not_found"] += 1
            with _fill_lock:
                _fill["done"] += 1
            time.sleep(0.25)  # be polite to LRCLIB
    except Exception as exc:
        log.error("Lyrics fill job failed: %s", exc)
    finally:
        with _fill_lock:
            _fill["running"] = False


@lyrics_bp.route("/api/lyrics/fill/start", methods=["POST"])
@login_required
def api_lyrics_fill_start():
    _check_csrf()
    st = state()
    retry = bool((request.get_json(force=True, silent=True) or {}).get("retry_not_found"))
    with _fill_lock:
        if _fill["running"]:
            return jsonify(ok=False, error="already_running"), 409
        _fill.update(running=True, total=0, done=0, filled=0, not_found=0, cancel=False)
    threading.Thread(target=_run_lyrics_fill, args=(st, retry),
                     name="lyrics-fill", daemon=True).start()
    return jsonify(ok=True)


@lyrics_bp.route("/api/lyrics/fill/cancel", methods=["POST"])
@login_required
def api_lyrics_fill_cancel():
    _check_csrf()
    with _fill_lock:
        _fill["cancel"] = True
    return jsonify(ok=True)


@lyrics_bp.route("/api/lyrics/fill/status")
@login_required
def api_lyrics_fill_status():
    with _fill_lock:
        return jsonify(running=_fill["running"], total=_fill["total"], done=_fill["done"],
                       filled=_fill["filled"], not_found=_fill["not_found"])
