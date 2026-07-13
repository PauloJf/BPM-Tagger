"""Track data + per-track mutation endpoints (save/unlock/approve/waveform)."""

import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import quote

import requests
from flask import Blueprint, Response, abort, jsonify, request, send_file

from ...bpm.tags import get_file_hash, write_bpm_tag
from ...bpm.waveform import compute_waveform_peaks
from ...grabber.matching import normalize_artist, normalize_title
from ...grabber.path_template import render, unique_path
from ...grabber.tagging import embed_cover, read_cover, resize_cover, write_track_tags
from ...integrations.isrc import gather_candidates, pick_confident
from ...integrations.metadata import gather_metadata
from ...integrations.navidrome import _trigger_navidrome_rescan
from ...integrations.ratelimit import deezer_limiter
from ...trash import move_to_trash, purge_trash, trash_stats
from ..auth import _check_csrf, login_required
from ..state import _assert_in_music_dir, state

log = logging.getLogger(__name__)

tracks_bp = Blueprint("api_tracks", __name__)


_ISRC_RE = re.compile(r"^[A-Za-z]{2}[A-Za-z0-9]{3}\d{7}$")


def _normalize_isrc(code: str) -> str:
    return re.sub(r"[\s-]", "", code or "").upper()


def _isrc_error(code: str):
    """Return an error string if a (non-empty) ISRC is malformed, else None."""
    if code and not _ISRC_RE.match(code):
        return "Invalid ISRC — expected 12 chars: 2-letter country, 3 alphanumeric, 2-digit year, 5-digit code."
    return None


def _parse_bpm_filter(args) -> tuple:
    """Return (bpm_target, bpm_tol) from request args, or (None, 5)."""
    bpm_target = None
    bpm_tol = 5.0
    bpm_str = args.get("bpm", "").strip()
    if bpm_str:
        try:
            bpm_target = float(bpm_str)
            bpm_tol = max(0.0, float(args.get("bpm_tol", "5")))
        except (ValueError, TypeError):
            pass
    return bpm_target, bpm_tol


@tracks_bp.route("/api/save_bpm", methods=["POST"])
@login_required
def api_save_bpm():
    _check_csrf()
    st = state()
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
        st.db.lock_track(file_path, bpm)
        if st.write_tags:
            write_bpm_tag(file_path, bpm, st.preserve_mtime)
        log.info("UI: locked %s at %.1f BPM", Path(file_path).name, bpm)
        return jsonify(ok=True)
    except Exception as exc:
        log.error("UI save_bpm error: %s", exc)
        return jsonify(ok=False, error=str(exc))


@tracks_bp.route("/api/track/star", methods=["POST"])
@login_required
def api_track_star():
    _check_csrf()
    st = state()
    data = request.get_json(force=True, silent=True) or {}
    path = str(data.get("path", ""))
    if not path:
        abort(400)
    _assert_in_music_dir(path)
    if not st.db.get_track(path):
        abort(404)
    starred = bool(data.get("starred"))
    st.db.set_starred(path, starred)
    return jsonify(ok=True, starred=starred)


@tracks_bp.route("/api/unlock", methods=["POST"])
@login_required
def api_unlock():
    _check_csrf()
    st = state()
    data = request.get_json(force=True, silent=True) or {}
    file_path = data.get("file_path", "")
    if not file_path:
        return jsonify(ok=False, error="file_path required")

    _assert_in_music_dir(file_path)

    try:
        st.db.unlock_track(file_path)
        log.info("UI: unlocked %s", Path(file_path).name)
        return jsonify(ok=True)
    except Exception as exc:
        return jsonify(ok=False, error=str(exc))


@tracks_bp.route("/api/approve", methods=["POST"])
@login_required
def api_approve():
    _check_csrf()
    st = state()
    data = request.get_json(force=True, silent=True) or {}
    file_path = data.get("file_path", "")
    if not file_path:
        return jsonify(ok=False, error="file_path required")
    _assert_in_music_dir(file_path)
    try:
        st.db.approve_track(file_path)
        log.info("UI: approved %s", Path(file_path).name)
        return jsonify(ok=True, review_count=st.db.get_stats().get("needs_review", 0))
    except Exception as exc:
        return jsonify(ok=False, error=str(exc))


@tracks_bp.route("/api/waveform")
@login_required
def api_waveform():
    st = state()
    path = request.args.get("path", "")
    if not path:
        abort(400)
    _assert_in_music_dir(path)

    # 1. In-memory cache (fastest)
    if path in st.waveform_cache:
        return jsonify(st.waveform_cache[path])

    # 2. DB — populated during BPM analysis so no extra librosa call needed
    if st.db:
        track = st.db.get_track(path)
        if track and track.get("waveform_peaks"):
            try:
                result = json.loads(track["waveform_peaks"])
                st.cache_waveform(path, result)
                return jsonify(result)
            except Exception:
                pass  # corrupt value — fall through to recompute

    # 3. Deduplicated librosa fallback (old tracks not yet in DB)
    #    Only one thread computes per path; others wait on the Event.
    with st.waveform_inflight_lock:
        if path in st.waveform_inflight:
            ev = st.waveform_inflight[path]
            leader = False
        else:
            ev = threading.Event()
            st.waveform_inflight[path] = ev
            leader = True

    if not leader:
        ev.wait(timeout=30)
        result = st.waveform_cache.get(path)
        if result:
            return jsonify(result)
        return jsonify(error="waveform not available"), 503

    try:
        raw = compute_waveform_peaks(path)
        if raw is None:
            return jsonify(error="waveform computation failed"), 500
        result = json.loads(raw)
        st.cache_waveform(path, result)
        if st.db:
            st.db.save_waveform_peaks(path, raw)
        return jsonify(result)
    except Exception as exc:
        log.warning("Waveform generation failed for %s: %s", Path(path).name, exc)
        return jsonify(error=str(exc)), 500
    finally:
        ev.set()
        with st.waveform_inflight_lock:
            st.waveform_inflight.pop(path, None)


@tracks_bp.route("/api/track")
@login_required
def api_track():
    """Single-track detail for the SPA TrackDetail page.

    Mirrors ``pages.track_detail``: detector values, confidence and lock state
    come straight from the row; prev/next are resolved within the review queue
    (``back=review``). Track-list navigation is handled client-side from the
    already-loaded page, matching the current Jinja UI.
    """
    st = state()
    path = request.args.get("path", "")
    if not path:
        abort(400)
    _assert_in_music_dir(path)
    track = st.db.get_track(path)
    if not track:
        abort(404)

    back = request.args.get("back", "tracks")
    if back not in ("tracks", "review"):
        back = "tracks"

    prev_path = next_path = None
    queue_pos = queue_total = None
    if back == "review":
        queue = [t["file_path"] for t in
                 st.db.get_suspicious(st.conf_threshold, 0, float("inf"))]
        queue_total = len(queue)
        try:
            idx = queue.index(path)
            queue_pos = idx + 1
            prev_path = queue[idx - 1] if idx > 0 else None
            next_path = queue[idx + 1] if idx < len(queue) - 1 else None
        except ValueError:
            pass

    return jsonify(track=track, back=back,
                   prev_path=prev_path, next_path=next_path,
                   queue_pos=queue_pos, queue_total=queue_total,
                   playback_buffer=st.config.get("playback_buffer", 3))


@tracks_bp.route("/api/artists")
@login_required
def api_artists():
    """Artist index for the library browse view."""
    return jsonify(artists=state().db.list_artists())


@tracks_bp.route("/api/albums")
@login_required
def api_albums():
    """Album index for the library browse view."""
    return jsonify(albums=state().db.list_albums())


@tracks_bp.route("/api/artist")
@login_required
def api_artist():
    """An artist's tracks (album-ordered) plus a small BPM summary."""
    name = request.args.get("name", "").strip()
    if not name:
        return jsonify(name="", tracks=[], stats={})
    rows = state().db.get_artist_tracks(name)
    bpms = [r["bpm"] for r in rows if r.get("bpm")]
    albums = sorted({(r.get("album") or "") for r in rows})
    stats = {
        "tracks": len(rows),
        "albums": len([a for a in albums if a]),
        "avg_bpm": round(sum(bpms) / len(bpms), 1) if bpms else None,
        "min_bpm": min(bpms) if bpms else None,
        "max_bpm": max(bpms) if bpms else None,
    }
    return jsonify(name=name, tracks=rows, stats=stats)


@tracks_bp.route("/api/album")
@login_required
def api_album():
    """An album's tracks (disc/track-ordered) + a small summary."""
    album = request.args.get("album", "").strip()
    album_artist = request.args.get("album_artist", "").strip()
    if not album:
        return jsonify(album="", album_artist="", tracks=[], stats={})
    rows = state().db.get_album_tracks(album, album_artist or None)
    bpms = [r["bpm"] for r in rows if r.get("bpm")]
    aa = album_artist or (rows[0].get("album_artist") if rows else "") or ""
    stats = {
        "tracks": len(rows),
        "avg_bpm": round(sum(bpms) / len(bpms), 1) if bpms else None,
        "year": (rows[0].get("year") if rows else None),
    }
    return jsonify(album=album, album_artist=aa, tracks=rows, stats=stats)


@tracks_bp.route("/api/duplicates")
@login_required
def api_duplicates():
    """Groups of library tracks sharing normalized artist+title (possible dupes)."""
    return jsonify(groups=state().db.get_duplicates())


@tracks_bp.route("/api/duplicates/dismiss", methods=["POST"])
@login_required
def api_duplicates_dismiss():
    """Mark a group as 'not a duplicate' so it stops appearing."""
    _check_csrf()
    paths = (request.get_json(force=True, silent=True) or {}).get("paths") or []
    if len(paths) < 2:
        return jsonify(ok=False, error="need at least two paths"), 400
    state().db.dismiss_duplicate(paths)
    return jsonify(ok=True)


@tracks_bp.route("/api/track/trash", methods=["POST"])
@login_required
def api_track_trash():
    """Soft-delete: move the file to the trash, mark the row deleted, and ask
    Navidrome to rescan so the removed track drops out of the library."""
    _check_csrf()
    st = state()
    file_path = (request.get_json(force=True, silent=True) or {}).get("file_path", "")
    if not file_path:
        return jsonify(ok=False, error="file_path required"), 400
    _assert_in_music_dir(file_path)
    row = st.db.get_track(file_path)
    if row and row.get("locked"):
        return jsonify(ok=False, error="Track is locked — unlock it before deleting."), 400
    try:
        if os.path.exists(file_path):
            move_to_trash(st.config, file_path)
        st.db.mark_deleted(file_path)
    except Exception as exc:
        log.error("UI trash error: %s", exc)
        return jsonify(ok=False, error=str(exc)), 500
    try:
        _trigger_navidrome_rescan(st.config)
    except Exception:
        pass
    return jsonify(ok=True)


@tracks_bp.route("/api/isrc/lookup")
@login_required
def api_isrc_lookup():
    """Find candidate ISRCs for an artist+title from Spotify (if connected) and
    MusicBrainz — used by the 'Find ISRC' action in the compare view."""
    st = state()
    artist = request.args.get("artist", "").strip()
    title = request.args.get("title", "").strip()
    query = f"{artist} {title}".strip()
    spotify_search_url = f"https://open.spotify.com/search/{quote(query)}" if query else ""
    if not query:
        return jsonify(candidates=[], spotify_search_url=spotify_search_url)

    g = getattr(st.tagger, "grabber", None) if st.tagger else None
    client = g.client if g else None
    candidates = gather_candidates(st.config, client, artist, title)
    return jsonify(candidates=candidates, spotify_search_url=spotify_search_url)


@tracks_bp.route("/api/metadata/lookup")
@login_required
def api_metadata_lookup():
    """Full-tag candidates for the metadata editor's 'Find metadata' action.
    With ?isrc= both sources resolve that exact recording; otherwise
    ?artist=/&title= (or free ?q=) run a text search."""
    st = state()
    isrc = _normalize_isrc(request.args.get("isrc", ""))
    if isrc and _isrc_error(isrc):
        return jsonify(candidates=[], error=_isrc_error(isrc)), 400
    artist = request.args.get("artist", "").strip()
    title = request.args.get("title", "").strip()
    q = request.args.get("q", "").strip()
    if not (isrc or artist or title or q):
        return jsonify(candidates=[])
    g = getattr(st.tagger, "grabber", None) if st.tagger else None
    client = g.client if g else None
    return jsonify(candidates=gather_metadata(client, artist=artist, title=title,
                                              isrc=isrc, q=q))


def _set_track_isrc(st, file_path: str, isrc: str) -> bool:
    """Write an ISRC onto a track, preserving its other metadata, and refresh the
    DB hash so the watcher won't re-analyze the file. Returns False if unknown."""
    track = st.db.get_track(file_path)
    if not track:
        return False
    tags = {k: track.get(k) for k in ("title", "artist", "album", "album_artist",
                                       "track_no", "disc_no", "year")}
    tags["isrc"] = (isrc or "").strip() or None
    tags["norm_title"] = normalize_title(tags["title"])
    tags["norm_artist"] = normalize_artist(tags["artist"])
    write_track_tags(file_path, tags)
    st.db.update_track_metadata(file_path, file_path, tags, get_file_hash(file_path))
    return True


@tracks_bp.route("/api/track/isrc", methods=["POST"])
@login_required
def api_track_isrc():
    """Set a single track's ISRC (used by the track-detail 'Find ISRC' picker and
    the bulk-fill unresolved list)."""
    _check_csrf()
    st = state()
    data = request.get_json(force=True, silent=True) or {}
    path = data.get("file_path", "")
    if not path:
        return jsonify(ok=False, error="file_path required"), 400
    _assert_in_music_dir(path)
    isrc = _normalize_isrc(data.get("isrc") or "")
    err = _isrc_error(isrc)
    if err:
        return jsonify(ok=False, error=err), 400
    try:
        if not _set_track_isrc(st, path, isrc):
            return jsonify(ok=False, error="not found"), 404
    except Exception as exc:
        log.error("UI set-isrc error: %s", exc)
        return jsonify(ok=False, error=str(exc)), 500
    return jsonify(ok=True)


# ── Bulk ISRC fill (background job) ──────────────────────────────────────────
_isrc_fill = {"running": False, "total": 0, "done": 0, "filled": 0, "unresolved": [], "cancel": False}
_isrc_fill_lock = threading.Lock()


def _run_isrc_fill(st):
    """Look up every missing-ISRC track; auto-write a confident (duration-matched)
    single match, else record it with its candidates for the user to choose."""
    try:
        tracks = st.db.get_tracks_missing_isrc(limit=2000)
        with _isrc_fill_lock:
            _isrc_fill.update(running=True, total=len(tracks), done=0, filled=0, unresolved=[], cancel=False)
        g = getattr(st.tagger, "grabber", None)
        client = g.client if g else None
        for t in tracks:
            with _isrc_fill_lock:
                if _isrc_fill["cancel"]:
                    break
            cands = gather_candidates(st.config, client, t.get("artist") or "", t.get("title") or "")
            isrc = pick_confident(cands, t.get("duration_ms"))
            if isrc:
                try:
                    _set_track_isrc(st, t["file_path"], isrc)
                    with _isrc_fill_lock:
                        _isrc_fill["filled"] += 1
                except Exception as exc:
                    log.warning("ISRC fill write failed for %s: %s", t.get("file_path"), exc)
            else:
                with _isrc_fill_lock:
                    _isrc_fill["unresolved"].append({
                        "file_path": t["file_path"], "title": t.get("title") or "",
                        "artist": t.get("artist") or "", "candidates": cands})
            with _isrc_fill_lock:
                _isrc_fill["done"] += 1
            time.sleep(0.2)  # throttle for external rate limits
    except Exception as exc:
        log.error("ISRC fill job failed: %s", exc)
    finally:
        with _isrc_fill_lock:
            _isrc_fill["running"] = False


@tracks_bp.route("/api/isrc/fill/start", methods=["POST"])
@login_required
def api_isrc_fill_start():
    _check_csrf()
    st = state()
    with _isrc_fill_lock:
        if _isrc_fill["running"]:
            return jsonify(ok=False, error="already_running"), 409
        _isrc_fill.update(running=True, total=0, done=0, filled=0, unresolved=[], cancel=False)
    threading.Thread(target=_run_isrc_fill, args=(st,), name="isrc-fill", daemon=True).start()
    return jsonify(ok=True)


@tracks_bp.route("/api/isrc/fill/cancel", methods=["POST"])
@login_required
def api_isrc_fill_cancel():
    _check_csrf()
    with _isrc_fill_lock:
        _isrc_fill["cancel"] = True
    return jsonify(ok=True)


@tracks_bp.route("/api/isrc/fill/status")
@login_required
def api_isrc_fill_status():
    with _isrc_fill_lock:
        return jsonify(running=_isrc_fill["running"], total=_isrc_fill["total"],
                       done=_isrc_fill["done"], filled=_isrc_fill["filled"],
                       unresolved=list(_isrc_fill["unresolved"]))


@tracks_bp.route("/api/trash")
@login_required
def api_trash():
    """Current trash contents (count + bytes) for the Settings panel."""
    return jsonify(trash_stats(state().config))


@tracks_bp.route("/api/trash/purge", methods=["POST"])
@login_required
def api_trash_purge():
    """Permanently empty the trash."""
    _check_csrf()
    return jsonify(ok=True, **purge_trash(state().config))


@tracks_bp.route("/api/deleted")
@login_required
def api_deleted():
    """Count of tracks whose files are gone (status='deleted') — for Settings."""
    return jsonify(count=state().db.count_deleted())


@tracks_bp.route("/api/deleted/purge", methods=["POST"])
@login_required
def api_deleted_purge():
    """Permanently drop all deleted-status rows from the database. Unrecoverable;
    touches no files on disk (they are already removed) — clears stale records only."""
    _check_csrf()
    purged = state().db.purge_deleted()
    return jsonify(ok=True, purged=purged)


@tracks_bp.route("/api/review")
@login_required
def api_review():
    """Paginated review queue (suspicious tracks) for the SPA Review page."""
    st = state()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    per_page = 50
    total = st.db.get_suspicious_count(st.conf_threshold, st.bpm_min, st.bpm_max)
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    rows = st.db.get_suspicious_page(st.conf_threshold, st.bpm_min, st.bpm_max,
                                     per_page, (page - 1) * per_page)
    return jsonify(tracks=rows, conf_threshold=st.conf_threshold,
                   total=total, page=page, pages=pages, per_page=per_page)


def _int_or_none(v):
    try:
        return int(str(v).split("/")[0].strip()) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


@tracks_bp.route("/api/track/tags", methods=["PUT"])
@login_required
def api_track_tags():
    """Rewrite descriptive tags; optionally rename to the path template. The DB
    hash is refreshed AFTER all writes so the watcher won't re-analyze the file."""
    _check_csrf()
    st = state()
    data = request.get_json(force=True, silent=True) or {}
    path = data.get("file_path", "")
    if not path:
        return jsonify(ok=False, error="file_path required"), 400
    _assert_in_music_dir(path)
    track = st.db.get_track(path)
    if not track:
        return jsonify(ok=False, error="not found"), 404

    isrc = _normalize_isrc(data.get("isrc") or "")
    err = _isrc_error(isrc)
    if err:
        return jsonify(ok=False, error=err), 400
    tags = {
        "title": (data.get("title") or "").strip() or None,
        "artist": (data.get("artist") or "").strip() or None,
        "album": (data.get("album") or "").strip() or None,
        "album_artist": (data.get("album_artist") or "").strip() or None,
        "track_no": _int_or_none(data.get("track_no")),
        "disc_no": _int_or_none(data.get("disc_no")),
        "year": _int_or_none(data.get("year")),
        "isrc": isrc or None,
    }
    tags["norm_title"] = normalize_title(tags["title"])
    tags["norm_artist"] = normalize_artist(tags["artist"])

    try:
        write_track_tags(path, tags)
        new_path = path
        if data.get("apply_template"):
            ext = os.path.splitext(path)[1].lstrip(".")
            template = st.config.get("path_template", "{AlbumArtist}/{Album}/{TrackNo:02d} - {Title}.{ext}")
            candidate = unique_path(st.music_dir, render(template, tags, ext))
            if os.path.abspath(candidate) != os.path.abspath(path):
                os.makedirs(os.path.dirname(candidate), exist_ok=True)
                os.replace(path, candidate)  # same filesystem (both under music_dir)
                new_path = candidate
        fresh_hash = get_file_hash(new_path)
        st.db.update_track_metadata(path, new_path, tags, fresh_hash)
        log.info("UI: edited tags for %s", Path(new_path).name)
        return jsonify(ok=True, file_path=new_path)
    except Exception as exc:
        log.error("Tag edit failed: %s", exc)
        return jsonify(ok=False, error=str(exc)), 500


@tracks_bp.route("/api/track/cover")
@login_required
def api_track_cover_get():
    path = request.args.get("path", "")
    if not path:
        abort(400)
    _assert_in_music_dir(path)
    # Cache on the file hash (size:mtime) so thumbnail grids don't re-extract
    # embedded art on every visit, while cover edits still bust the cache.
    try:
        etag = f'"{get_file_hash(path)}"'
    except OSError:
        abort(404)
    cache_headers = {"Cache-Control": "private, max-age=86400", "ETag": etag}
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304, headers=cache_headers)
    cover = read_cover(path)
    if not cover:
        abort(404)
    data, mime = cover
    return Response(data, mimetype=mime, headers=cache_headers)


_ARTIST_IMG_NAMES = ("artist.jpg", "artist.jpeg", "artist.png", "artist.webp")
_ARTIST_IMG_MISS_TTL = 86400  # retry failed online lookups daily


def _artist_image_cache(name: str) -> tuple[str, str, str, str]:
    """(cache_dir, custom_image_path, cached_image_path, miss_marker_path) for an
    artist name. ``custom`` holds an image the user explicitly chose in the UI;
    ``cached`` holds an automatic online-lookup result."""
    data_dir = os.path.dirname(state().config.get("db_path", "/data/bpm_tagger.db")) or "."
    cache_dir = os.path.join(data_dir, "artist_images")
    slug = hashlib.sha1(name.strip().lower().encode("utf-8")).hexdigest()[:16]
    return (cache_dir, os.path.join(cache_dir, slug + ".custom"),
            os.path.join(cache_dir, slug + ".img"), os.path.join(cache_dir, slug + ".miss"))


def _resolve_artist_dir(st, name: str) -> str | None:
    """The artist's own folder inside the library, or None when the layout
    doesn't have one. Only a directory that exclusively contains this artist's
    tracks qualifies, so flat or shared (compilation) layouts never get a stray
    artist.jpg written into them."""
    rows = st.db.get_artist_tracks(name)
    dirs = sorted({os.path.dirname(r["file_path"]) for r in rows if r.get("file_path")})
    if not dirs:
        return None
    try:
        common = os.path.commonpath(dirs)
    except ValueError:  # paths on different drives
        return None
    music_root = os.path.realpath(st.music_dir)

    def inside_library(d: str) -> bool:
        rd = os.path.realpath(d)
        return rd != music_root and rd.startswith(music_root + os.sep)

    def exclusive(d: str) -> bool:
        for t in st.db.get_tracks_under(os.path.join(d, "")):
            owner = t.get("album_artist") or t.get("artist") or ""
            if owner != name and (t.get("artist") or "") != name:
                return False
        return True

    # For a single-album artist ({Artist}/{Album}/…) commonpath is the album
    # dir — prefer its parent (the artist dir) when that's still safe.
    for candidate in (os.path.dirname(common), common):
        if candidate and inside_library(candidate) and os.path.isdir(candidate) \
                and exclusive(candidate):
            return candidate
    return None


def _save_artist_image_to_library(st, name: str, image: bytes) -> str | None:
    """Write an artist image as artist.jpg in the artist's own folder
    (Navidrome's convention), when ``artist_images_to_library`` is on and the
    layout has a folder dedicated to the artist. Returns the saved path."""
    if not st.config.get("artist_images_to_library") or not image:
        return None
    target_dir = _resolve_artist_dir(st, name)
    if not target_dir:
        return None
    path = os.path.join(target_dir, "artist.jpg")
    try:
        with open(path, "wb") as f:
            f.write(image)
        log.info("Artist image saved to library: %s", path)
        return path
    except OSError as exc:
        log.warning("Could not save artist.jpg for %s: %s", name, exc)
        return None


@tracks_bp.route("/api/artist/image")
@login_required
def api_artist_image():
    """Artist image, resolved in privacy-preserving order: a user-chosen custom
    image (set via the image picker) → an ``artist.jpg`` beside the artist's
    files (Navidrome convention) → the on-disk cache → an online Deezer lookup
    (opt-in via ``fetch_artist_images``), cached to disk so each artist is
    fetched at most once a day. 404 lets the client fall back to album art."""
    st = state()
    name = request.args.get("name", "").strip()
    if not name:
        abort(400)

    # 0. Explicit user choice always wins.
    cache_dir, custom, cached, miss = _artist_image_cache(name)
    if os.path.isfile(custom):
        return send_file(custom, mimetype="image/jpeg", conditional=True, max_age=86400)

    # 1. Local artist.<ext> in the track's folder or its parent.
    seen: set[str] = set()
    for row in st.db.get_artist_tracks(name):
        track_dir = os.path.dirname(row["file_path"])
        for d in (track_dir, os.path.dirname(track_dir)):
            if not d or d in seen:
                continue
            seen.add(d)
            for fname in _ARTIST_IMG_NAMES:
                p = os.path.join(d, fname)
                if os.path.isfile(p):
                    return send_file(p, conditional=True, max_age=86400)

    # 2. Previously fetched image on disk.
    if os.path.isfile(cached):
        return send_file(cached, mimetype="image/jpeg", conditional=True, max_age=86400)

    # 3. Online lookup — only when the user opted in, and not re-tried while a
    # recent miss marker exists (protects Deezer and page loads alike).
    if not st.config.get("fetch_artist_images"):
        abort(404)
    if os.path.isfile(miss) and time.time() - os.path.getmtime(miss) < _ARTIST_IMG_MISS_TTL:
        abort(404)
    os.makedirs(cache_dir, exist_ok=True)
    try:
        deezer_limiter.acquire()  # stay under Deezer's public 50 req / 5 s quota
        resp = requests.get("https://api.deezer.com/search/artist",
                            params={"q": name, "limit": 1}, timeout=8)
        resp.raise_for_status()
        hits = resp.json().get("data") or []
        hit = hits[0] if hits else {}
        url = hit.get("picture_xl") or hit.get("picture_big") or ""
        # Only accept an exact (caseless) name match — a wrong artist's photo
        # is worse than the album-art fallback. Deezer's placeholder images
        # live under /artist//, which also signals "no real picture".
        if url and "/artist//" not in url and hit.get("name", "").strip().lower() == name.lower():
            img = requests.get(url, timeout=10)
            img.raise_for_status()
            # Optionally file it as artist.jpg beside the artist's music
            # (Navidrome sees it too, and step 1 finds it on every next load).
            saved = _save_artist_image_to_library(st, name, img.content)
            if saved:
                return send_file(saved, mimetype="image/jpeg", conditional=True, max_age=86400)
            with open(cached, "wb") as f:
                f.write(img.content)
            return send_file(cached, mimetype="image/jpeg", conditional=True, max_age=86400)
    except Exception as exc:
        log.debug("Artist image lookup failed for %s: %s", name, exc)
    with open(miss, "wb"):
        pass
    abort(404)


@tracks_bp.route("/api/track/cover", methods=["PUT"])
@login_required
def api_track_cover_put():
    _check_csrf()
    st = state()
    path = request.args.get("path", "")
    if not path:
        return jsonify(ok=False, error="path required"), 400
    _assert_in_music_dir(path)
    if not st.db.get_track(path):
        return jsonify(ok=False, error="not found"), 404
    image = request.get_data()
    if not image:
        return jsonify(ok=False, error="empty body"), 400
    try:
        image = resize_cover(image)  # normalize to <=1200px (JPEG if re-encoded)
        embed_cover(path, image)  # MIME sniffed from the actual bytes
        st.db.refresh_track_hash(path, get_file_hash(path))  # hash only; keep tags
        return jsonify(ok=True)
    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 500


@tracks_bp.route("/api/tracks")
@login_required
def api_tracks():
    st = state()
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
    filter_by = request.args.get("filter", "")
    bpm_target, bpm_tol = _parse_bpm_filter(request.args)
    cadence = request.args.get("bpm_cadence") in ("1", "true", "yes")
    rows, total = st.db.get_tracks_page(q, per_page, (page - 1) * per_page,
                                        filter=filter_by,
                                        bpm_target=bpm_target, bpm_tol=bpm_tol, bpm_cadence=cadence)
    pages = max(1, (total + per_page - 1) // per_page)
    stats = st.db.get_stats()
    return jsonify(tracks=rows, total=total, page=page, pages=pages, per_page=per_page,
                   filter=filter_by,
                   all_count=stats.get("total", 0),
                   review_count=stats.get("needs_review", 0),
                   locked_count=stats.get("locked", 0),
                   deleted_count=stats.get("deleted", 0),
                   no_isrc_count=stats.get("missing_isrc", 0),
                   starred_count=stats.get("starred", 0))


@tracks_bp.route("/api/tracks/paths")
@login_required
def api_track_paths():
    """Ordered path list for the current filter — used by the player's Play All /
    Shuffle so the queue matches the library view (capped in the DB layer)."""
    st = state()
    q = request.args.get("q", "").strip()
    filter_by = request.args.get("filter", "")
    bpm_target, bpm_tol = _parse_bpm_filter(request.args)
    cadence = request.args.get("bpm_cadence") in ("1", "true", "yes")
    rows = st.db.get_track_paths(q, filter=filter_by, bpm_target=bpm_target, bpm_tol=bpm_tol, bpm_cadence=cadence)
    return jsonify(tracks=rows, count=len(rows))
