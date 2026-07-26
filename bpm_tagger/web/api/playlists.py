"""Playlist endpoints (§3): list/add/patch/delete, tracks (have|missing|queued),
manual sync. Also the consolidated grabber status poll."""

import hashlib
import io
import logging
import os
import re

from flask import Blueprint, Response, abort, jsonify, request, send_file

from ...config import __version__
from ...grabber.spotify import SpotifyError, parse_playlist_id
from ...grabber.tagging import read_cover
from ..auth import _check_csrf, login_required
from ..state import _assert_in_music_dir, state
from .images import _image_from_request


def _versions() -> dict:
    v = {"app": __version__, "yt_dlp": None}
    try:
        import yt_dlp
        v["yt_dlp"] = getattr(getattr(yt_dlp, "version", None), "__version__", None)
    except Exception:
        pass
    return v

log = logging.getLogger(__name__)

playlists_bp = Blueprint("api_playlists", __name__)


def _grabber():
    st = state()
    return getattr(st.tagger, "grabber", None) if st.tagger else None


@playlists_bp.route("/api/grabber/status")
@login_required
def grabber_status():
    g = _grabber()
    if not g:
        return jsonify(enabled=False)
    db = state().db
    counts = db.get_queue_counts()
    return jsonify(
        enabled=True,
        spotify=g.status(),
        queue_counts=counts,
        active=db.get_active_grabs(),
        inbox_count=counts.get("awaiting_user", 0),
        last_change=db.get_last_change(),
        versions=_versions(),
    )


@playlists_bp.route("/api/playlists", methods=["GET"])
@login_required
def list_playlists():
    return jsonify(playlists=state().db.list_playlists())


@playlists_bp.route("/api/playlists", methods=["POST"])
@login_required
def add_playlist():
    _check_csrf()
    data = request.get_json(force=True, silent=True) or {}
    source = str(data.get("source") or "spotify").lower()
    if source == "navidrome":
        return _add_navidrome_playlist(data)
    if source == "local":
        return _add_local_playlist(data)
    return _add_spotify_playlist(data)


def _add_local_playlist(data):
    """Create an empty Local playlist. No grabber/Spotify needed — Local is authored
    in-app by adding library tracks (see add_local_track), never synced from a source."""
    name = str(data.get("name") or "").strip()
    if not name:
        return jsonify(error="A playlist name is required."), 400
    db = state().db
    row_id = db.add_local_playlist(name)
    return jsonify(ok=True, playlist=db.get_playlist(row_id))


def _add_spotify_playlist(data):
    g = _grabber()
    if not g:
        return jsonify(error="grabber_disabled"), 409
    if not g.client.is_connected():
        return jsonify(error="not_connected"), 400
    pid = parse_playlist_id(str(data.get("url") or data.get("id") or ""))
    if not pid:
        return jsonify(error="A Spotify playlist URL or ID is required."), 400
    try:
        meta = g.client.get_playlist_meta(pid)
    except SpotifyError as exc:
        return jsonify(error=str(exc)), 400
    row_id = state().db.add_playlist(meta["spotify_id"], meta["name"],
                                     meta["image_url"], meta["track_count"])
    g.request_sync()
    return jsonify(ok=True, playlist=state().db.get_playlist(row_id))


def _add_navidrome_playlist(data):
    from ...integrations.navidrome_playlists import navidrome_configured, sync_navidrome_playlist
    cfg = state().config
    if not navidrome_configured(cfg):
        return jsonify(error="navidrome_not_configured"), 400
    nid = str(data.get("navidrome_id") or data.get("id") or "").strip()
    if not nid:
        return jsonify(error="A Navidrome playlist id is required."), 400
    db = state().db
    row_id = db.add_navidrome_playlist(nid, str(data.get("name") or ""))
    try:
        sync_navidrome_playlist(db, cfg, row_id)
    except Exception as exc:
        log.warning("Navidrome playlist add/sync failed: %s", exc)
        return jsonify(error=str(exc)), 400
    return jsonify(ok=True, playlist=db.get_playlist(row_id))


@playlists_bp.route("/api/navidrome/playlists")
@login_required
def navidrome_my_playlists():
    """Importable Navidrome playlists (the configured user's own + public),
    flagged with watched state. Independent of the grabber."""
    from ...integrations.navidrome_playlists import list_navidrome_playlists, navidrome_configured
    cfg = state().config
    if not navidrome_configured(cfg):
        return jsonify(error="navidrome_not_configured"), 400
    try:
        playlists = list_navidrome_playlists(cfg)
    except Exception as exc:
        log.warning("Navidrome playlist listing failed: %s", exc)
        return jsonify(error=str(exc)), 400
    watched = {p.get("navidrome_id") for p in state().db.list_playlists()
               if p.get("source") == "navidrome"}
    for p in playlists:
        p["watched"] = p["navidrome_id"] in watched
    return jsonify(playlists=playlists)


@playlists_bp.route("/api/playlists/<int:pid>", methods=["PATCH"])
@login_required
def patch_playlist(pid):
    """Update a playlist's user-editable fields. Any subset of enabled / name /
    description / pinned.

    Renaming is Local-only: a synced source rewrites `name` on every sync, so a
    renamed mirror would silently revert — better to refuse than to lie. The
    description and pinned flag are never touched by sync, so they're editable on
    any source."""
    _check_csrf()
    db = state().db
    pl = db.get_playlist(pid)
    if not pl:
        return jsonify(error="not_found"), 404
    data = request.get_json(force=True, silent=True) or {}

    meta = {}
    if "name" in data:
        if pl.get("source") != "local":
            return jsonify(error="Only local playlists can be renamed — a synced "
                                 "playlist takes its name from its source."), 400
        name = str(data["name"] or "").strip()
        if not name:
            return jsonify(error="A playlist name is required."), 400
        if len(name) > 200:
            return jsonify(error="That name is too long (200 characters max)."), 400
        meta["name"] = name
    if "description" in data:
        description = str(data["description"] or "").strip()
        if len(description) > 1000:
            return jsonify(error="That description is too long (1000 characters max)."), 400
        meta["description"] = description
    if "pinned" in data:
        meta["pinned"] = bool(data["pinned"])

    if "enabled" in data:
        db.set_playlist_enabled(pid, bool(data["enabled"]))
    if meta:
        db.update_playlist_meta(pid, **meta)
    return jsonify(ok=True, playlist=db.get_playlist(pid, with_counts=True))


@playlists_bp.route("/api/playlists/<int:pid>", methods=["DELETE"])
@login_required
def delete_playlist(pid):
    _check_csrf()
    state().db.delete_playlist(pid)
    return jsonify(ok=True)


@playlists_bp.route("/api/playlists/<int:pid>/tracks")
@login_required
def playlist_tracks(pid):
    db = state().db
    if not db.get_playlist(pid):
        return jsonify(error="not_found"), 404
    status = request.args.get("status", "")
    if status not in ("", "have", "missing", "queued", "removed"):
        status = ""
    tracks = db.get_playlist_tracks(pid, status)
    if not status:
        # Full detail view acknowledges the "new" badges.
        db.mark_playlist_seen(pid)
    # Enrich with coverage counts here (get_playlist alone omits them) so the detail
    # page's chips, tab list, and have-dependent header actions render. Read after
    # mark_playlist_seen so new_count reflects the just-cleared badges.
    return jsonify(playlist=db.get_playlist(pid, with_counts=True), tracks=tracks)


@playlists_bp.route("/api/playlists/<int:pid>/tracks", methods=["POST"])
@login_required
def add_local_track(pid):
    """Add a library track to a Local playlist (the "Add to playlist" action). Sets
    match_status='have' directly — a local track is by definition present. Local only:
    synced sources manage their own membership. Admin-only (not in _PLAYER_ALLOWED)."""
    _check_csrf()
    db = state().db
    pl = db.get_playlist(pid)
    if not pl:
        return jsonify(error="not_found"), 404
    if pl.get("source") != "local":
        return jsonify(error="Only local playlists accept manual track adds."), 400
    data = request.get_json(force=True, silent=True) or {}
    path = str(data.get("path") or "").strip()
    if not path:
        return jsonify(error="A track path is required."), 400
    _assert_in_music_dir(path)
    try:
        added = db.add_track_to_local_playlist(pid, path)
    except ValueError:
        return jsonify(error="track_not_found"), 404
    return jsonify(ok=True, added=added, playlist=db.get_playlist(pid))


@playlists_bp.route("/api/playlists/<int:pid>/import", methods=["POST"])
@login_required
def import_into_local(pid):
    """Bulk-copy every library-backed track from another playlist into this Local
    playlist, skipping duplicates. The source may be any playlist (Spotify /
    Navidrome / Local); the destination must be Local. Admin-only (not in
    _PLAYER_ALLOWED). Returns per-track counts for the client toast."""
    _check_csrf()
    db = state().db
    dest = db.get_playlist(pid)
    if not dest:
        return jsonify(error="not_found"), 404
    if dest.get("source") != "local":
        return jsonify(error="Only local playlists can be imported into."), 400
    data = request.get_json(force=True, silent=True) or {}
    try:
        src_id = int(data.get("from_playlist_id"))
    except (TypeError, ValueError):
        return jsonify(error="A source playlist id is required."), 400
    if src_id == pid:
        return jsonify(error="Can't import a playlist into itself."), 400
    if not db.get_playlist(src_id):
        return jsonify(error="source_not_found"), 404
    counts = db.import_playlist_tracks(pid, src_id)
    return jsonify(ok=True, counts=counts, playlist=db.get_playlist(pid))


@playlists_bp.route("/api/playlists/<int:pid>/tracks/<int:pt_id>", methods=["DELETE"])
@login_required
def remove_local_track(pid, pt_id):
    """Remove a track from a Local playlist (explicit hard-delete, with the client
    confirming). Local only — synced sources drop tracks via their next sync."""
    _check_csrf()
    db = state().db
    pl = db.get_playlist(pid)
    if not pl:
        return jsonify(error="not_found"), 404
    if pl.get("source") != "local":
        return jsonify(error="Only local playlists support removing individual tracks."), 400
    db.remove_playlist_track(pt_id)
    return jsonify(ok=True, playlist=db.get_playlist(pid))


@playlists_bp.route("/api/playlists/<int:pid>/export.m3u")
@login_required
def export_m3u(pid):
    db = state().db
    pl = db.get_playlist(pid)
    if not pl:
        return jsonify(error="not_found"), 404
    lines = ["#EXTM3U"]
    for r in db.get_playlist_track_rows(pid):
        if r.get("matched_file_path"):
            dur = int((r.get("duration_ms") or 0) / 1000)
            lines.append(f"#EXTINF:{dur},{r.get('artist', '')} - {r.get('title', '')}")
            lines.append(r["matched_file_path"])
    body = "\n".join(lines) + "\n"
    fname = re.sub(r'[^\w.-]+', "_", pl.get("name") or "playlist") or "playlist"
    return Response(body, mimetype="audio/x-mpegurl",
                    headers={"Content-Disposition": f'attachment; filename="{fname}.m3u"'})


# ── Playlist cover art ────────────────────────────────────────────────────────
#
# Local playlists are created with image_url = '' and would otherwise show the ♪
# placeholder forever. Two layers, neither of which writes image_url — that column
# stays the synced sources' CDN URL, so a Spotify/Navidrome mirror keeps rendering
# its own art and a sync can never clobber a cover the user chose:
#
#   1. a custom cover the user set, stored as a file next to the DB, and
#   2. failing that, an auto-collage of the playlist's own tracks' embedded art.
#
# Both are Local-only for the same reason renaming is: sync owns a mirror's art.

# Composed collages, keyed by the identity of the source covers (see
# _collage_key). A membership or artwork change yields a new key, so there are no
# stale files to invalidate — the old entry is simply never looked up again.
_collage_cache: dict[tuple, bytes] = {}
_COLLAGE_PX = 600
_COLLAGE_TILES = 4


def _playlist_cover_path(pid: int) -> tuple[str, str]:
    """(directory, file) for a playlist's custom cover. Rooted next to the DB,
    the same way the artist-image cache is."""
    data_dir = os.path.dirname(state().config.get("db_path", "/data/bpm_tagger.db")) or "."
    cache_dir = os.path.join(data_dir, "playlist_covers")
    return cache_dir, os.path.join(cache_dir, f"{pid}.jpg")


def _collage_sources(pid: int) -> list[tuple[str, bytes]]:
    """Up to four (path, cover_bytes) for a playlist's matched tracks, in position
    order. Deduped by album so a single album can't fill the whole grid, and by
    the cover bytes themselves for libraries where albums aren't tagged."""
    out: list[tuple[str, bytes]] = []
    seen_albums: set[str] = set()
    seen_covers: set[bytes] = set()
    for r in state().db.get_playlist_tracks(pid):
        if len(out) >= _COLLAGE_TILES:
            break
        if r.get("derived_status") != "have":
            continue
        path = r.get("matched_file_path")
        if not path:
            continue
        album = (r.get("local_album") or r.get("album") or "").strip().lower()
        if album and album in seen_albums:
            continue
        cover = read_cover(path)
        if not cover or not cover[0]:
            continue
        digest = hashlib.sha1(cover[0]).digest()
        if digest in seen_covers:
            continue
        seen_covers.add(digest)
        if album:
            seen_albums.add(album)
        out.append((path, cover[0]))
    return out


def _collage_key(pid: int, sources: list[tuple[str, bytes]]) -> tuple:
    """Cache/ETag identity: the playlist plus each source file's path and
    size:mtime. The stat catches an artwork edit that leaves membership alone;
    a file we can't stat degrades to the path, which still tracks membership."""
    parts = []
    for path, _ in sources:
        try:
            st_ = os.stat(path)
            parts.append((path, st_.st_size, int(st_.st_mtime)))
        except OSError:
            parts.append((path, None, None))
    return (pid, tuple(parts))


def _compose_collage(sources: list[tuple[str, bytes]]) -> bytes | None:
    """A 2x2 grid of the four covers. Needs Pillow and exactly four tiles;
    anything else (including no Pillow at all) returns None and the caller serves
    the single first cover instead."""
    if len(sources) < _COLLAGE_TILES:
        return None
    try:
        from PIL import Image

        half = _COLLAGE_PX // 2
        grid = Image.new("RGB", (_COLLAGE_PX, _COLLAGE_PX))
        for i, (_, data) in enumerate(sources[:_COLLAGE_TILES]):
            tile = Image.open(io.BytesIO(data)).convert("RGB").resize((half, half))
            grid.paste(tile, ((i % 2) * half, (i // 2) * half))
        out = io.BytesIO()
        grid.save(out, format="JPEG", quality=85)
        return out.getvalue()
    except Exception as exc:
        log.debug("Playlist collage composition skipped: %s", exc)
        return None


@playlists_bp.route("/api/playlists/<int:pid>/cover", methods=["POST", "PUT"])
@login_required
def set_playlist_cover(pid):
    """Set a Local playlist's cover — JSON {url} (fetched server-side through the
    SSRF-guarded helper) or a raw body upload. Local-only: a synced playlist's art
    belongs to its source, same rationale as renaming."""
    _check_csrf()
    pl = state().db.get_playlist(pid)
    if not pl:
        return jsonify(ok=False, error="not_found"), 404
    if pl.get("source") != "local":
        return jsonify(ok=False, error="Only local playlists can have a custom cover — "
                                       "a synced playlist takes its art from its source."), 400
    data = request.get_json(silent=True) if request.is_json else None
    image, err = _image_from_request(data)
    if not image:
        return jsonify(ok=False, error=err), 400
    cache_dir, dest = _playlist_cover_path(pid)
    os.makedirs(cache_dir, exist_ok=True)
    with open(dest, "wb") as f:
        f.write(image)
    log.info("UI: set custom cover for playlist %s", pid)
    return jsonify(ok=True)


@playlists_bp.route("/api/playlists/<int:pid>/cover", methods=["DELETE"])
@login_required
def delete_playlist_cover(pid):
    """Drop the custom cover; the playlist falls back to its auto-collage."""
    _check_csrf()
    _, dest = _playlist_cover_path(pid)
    removed = False
    try:
        if os.path.isfile(dest):
            os.remove(dest)
            removed = True
    except OSError as exc:
        return jsonify(ok=False, error=str(exc)), 500
    return jsonify(ok=True, removed=removed)


@playlists_bp.route("/api/playlists/<int:pid>/cover")
@login_required
def get_playlist_cover(pid):
    """Serve the custom cover, else an auto-collage of the playlist's own tracks.

    Non-local playlists have no custom cover and no collage — they render their
    source's image_url on the client, so this 404s and the <img> onError falls
    back to the ♪ placeholder."""
    pl = state().db.get_playlist(pid)
    if not pl:
        abort(404)
    _, custom = _playlist_cover_path(pid)
    if os.path.isfile(custom):
        return send_file(custom, mimetype="image/jpeg", conditional=True, max_age=86400)
    if pl.get("source") != "local":
        abort(404)

    sources = _collage_sources(pid)
    if not sources:
        abort(404)
    key = _collage_key(pid, sources)
    etag = f'"{hashlib.sha1(repr(key).encode("utf-8")).hexdigest()}"'
    headers = {"Cache-Control": "private, max-age=86400", "ETag": etag}
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304, headers=headers)
    body = _collage_cache.get(key)
    if body is None:
        # Fewer than four covers (or no Pillow) → the first cover alone, rather
        # than a lopsided grid or nothing at all.
        body = _compose_collage(sources) or sources[0][1]
        _collage_cache[key] = body
    return Response(body, mimetype="image/jpeg", headers=headers)


@playlists_bp.route("/api/playlists/<int:pid>/sync", methods=["POST"])
@login_required
def sync_playlist(pid):
    _check_csrf()
    db = state().db
    pl = db.get_playlist(pid)
    if not pl:
        return jsonify(error="not_found"), 404
    if pl.get("source") == "local":
        return jsonify(error="Local playlists don't sync."), 400
    if pl.get("source") == "navidrome":
        from ...integrations.navidrome_playlists import navidrome_configured, sync_navidrome_playlist
        cfg = state().config
        if not navidrome_configured(cfg):
            return jsonify(error="navidrome_not_configured"), 400
        try:
            pl = sync_navidrome_playlist(db, cfg, pid)
        except Exception as exc:
            log.warning("Navidrome playlist sync failed: %s", exc)
            return jsonify(error=str(exc)), 400
        return jsonify(ok=True, playlist=pl)
    # Spotify (default): needs the grabber + a live connection.
    g = _grabber()
    if not g:
        return jsonify(error="grabber_disabled"), 409
    if not g.client.is_connected():
        return jsonify(error="not_connected"), 400
    try:
        pl = g.sync.sync_playlist(pid)
    except SpotifyError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(ok=True, playlist=pl)
