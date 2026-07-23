"""Suggestions + Related endpoints (Deezer-derived, keyless).

Part A — the /suggestions grabber page: stored artist/track recommendations,
manual + auto refresh, dismiss, lazy per-artist top tracks, and enqueue. All
grabber-gated (409 when the grabber is off).

Part B — the Related panel on Artist/Album/Track pages: live similar-artists /
similar-tracks lookups. These are ``login_required`` only (NOT grabber-gated):
pure read-only Deezer lookups that stay useful for navigating your own library
even with the grabber off. Only the enqueue action (Part A) is grabber-gated.

A tiny in-memory TTL cache holds the raw Deezer payloads for the Related
endpoints so navigating between artists doesn't re-hit Deezer; in_library /
queued flags are recomputed per request on top of the cached payload (the
library changes, the catalog doesn't).
"""

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

from ...grabber.enqueue import enqueue_track
from ...grabber.matching import library_match, normalize_artist, normalize_title
from ...grabber.suggestions import SEED_LIMIT, TTL_DAYS, build_library_artists
from ...integrations import deezer_catalog as dz
from ..auth import _check_csrf, login_required
from ..state import state

log = logging.getLogger(__name__)

suggestions_bp = Blueprint("api_suggestions", __name__)


def _grabber():
    st = state()
    return getattr(st.tagger, "grabber", None) if st.tagger else None


# ── shared flagging ─────────────────────────────────────────────────────────
def _queued_norm_keys(db) -> set:
    """(norm_title, norm_artist) for every non-terminal grab-queue item — used to
    flag suggested/related tracks whose recording is already downloading."""
    return {(normalize_title(g.get("title")), normalize_artist(g.get("artist")))
            for g in db.get_active_grabs()}


def _flag_tracks(tracks: list[dict], db) -> list[dict]:
    """Add live ``in_library`` (+ ``file_path``/``bpm`` when matched) and ``queued``
    flags to a list of track dicts (title/artist/album/duration_ms). Mutates in
    place. ``bpm`` lets the frontend enqueue a matched track straight into the
    play queue (the tempo lock stretches by BPM — see Part D of the plan)."""
    queued_keys = _queued_norm_keys(db)
    for t in tracks:
        title = t.get("title") or t.get("name") or ""
        artist = t.get("artist") or ""
        meta = {"title": title, "artist": artist, "album": t.get("album"),
                "duration_ms": t.get("duration_ms"), "isrc": ""}
        path = library_match(meta, db)
        t["in_library"] = bool(path)
        if path:
            t["file_path"] = path
            row = db.get_track(path)
            t["bpm"] = row.get("bpm") if row else None
        key = (normalize_title(title), normalize_artist(artist))
        t["queued"] = bool(t.get("queued_at")) or key in queued_keys
    return tracks


def _ser_artist(row: dict) -> dict:
    return {"id": row["id"], "dz_id": row["dz_id"], "name": row["name"],
            "image_url": row.get("image_url") or "",
            "have_tracks": row.get("have_tracks") or 0,
            "seeds": row.get("seeds") or [], "score": row.get("score") or 0}


def _ser_track(row: dict) -> dict:
    return {"id": row["id"], "dz_track_id": row["dz_id"], "title": row["name"],
            "artist": row.get("artist") or "", "album": row.get("album") or "",
            "duration_ms": row.get("duration_ms"),
            "cover_url": row.get("image_url") or "",
            "preview_url": row.get("preview_url") or "",
            "seeds": row.get("seeds") or [], "queued_at": row.get("queued_at")}


def _is_stale(computed_at: str) -> bool:
    """True when there are no suggestions yet or they're older than TTL_DAYS."""
    if not computed_at:
        return True
    try:
        dt = datetime.fromisoformat(computed_at)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - dt > timedelta(days=TTL_DAYS)


# ══════════════════════════════════════════════════════════════════════════
# Part A — /suggestions page (grabber-gated)
# ══════════════════════════════════════════════════════════════════════════
@suggestions_bp.route("/api/suggestions")
@login_required
def get_suggestions():
    g = _grabber()
    if not g:
        return jsonify(enabled=False)
    db = state().db
    engine = g.suggestions
    computed_at = db.suggestions_computed_at()
    if _is_stale(computed_at) and not engine.refreshing:
        engine.refresh_async()
    artists = [_ser_artist(r) for r in db.get_suggestions("artist")]
    tracks = [_ser_track(r) for r in db.get_suggestions("track")]
    _flag_tracks(tracks, db)
    seed_count = min(len(build_library_artists(db)), SEED_LIMIT)
    return jsonify(enabled=True, artists=artists, tracks=tracks,
                   refreshing=engine.refreshing, last_error=engine.last_error,
                   computed_at=computed_at, seed_count=seed_count)


@suggestions_bp.route("/api/suggestions/refresh", methods=["POST"])
@login_required
def refresh_suggestions():
    _check_csrf()
    g = _grabber()
    if not g:
        return jsonify(error="grabber_disabled"), 409
    if not g.suggestions.refresh_async():
        return jsonify(error="already_refreshing"), 409
    return jsonify(ok=True)


@suggestions_bp.route("/api/suggestions/dismiss", methods=["POST"])
@login_required
def dismiss_suggestion():
    _check_csrf()
    g = _grabber()
    if not g:
        return jsonify(error="grabber_disabled"), 409
    data = request.get_json(force=True, silent=True) or {}
    kind = data.get("kind")
    key = data.get("key")
    if kind not in ("artist", "track") or not key:
        return jsonify(error="kind and key required"), 400
    # Artists are dismissed by normalized name (stable across refreshes/sources),
    # so the client can pass the display name; tracks by Deezer track id.
    key = normalize_artist(str(key)) if kind == "artist" else str(key)
    state().db.dismiss_suggestion(kind, key)
    return jsonify(ok=True)


@suggestions_bp.route("/api/suggestions/artists/<dz_id>/tracks")
@login_required
def suggestion_artist_tracks(dz_id):
    """Live top tracks for one suggested artist (lazy expansion; not persisted)."""
    g = _grabber()
    if not g:
        return jsonify(error="grabber_disabled"), 409
    tracks = dz.artist_top_tracks(dz_id, limit=8)
    _flag_tracks(tracks, state().db)
    return jsonify(tracks=tracks)


@suggestions_bp.route("/api/suggestions/queue", methods=["POST"])
@login_required
def queue_suggestion():
    """Enqueue one suggested Deezer track into the grab pipeline.

    Deezer tracks carry no spotify_track_id (which ``enqueue_grab`` dedupes on),
    so when Spotify is connected we adopt a confident (≥0.9) search match's
    spotify_track_id/ISRC for full dedupe + better provider matching; otherwise
    we fetch the ISRC straight from Deezer. Both lookups are best-effort."""
    _check_csrf()
    g = _grabber()
    if not g:
        return jsonify(error="grabber_disabled"), 409
    data = request.get_json(force=True, silent=True) or {}
    dz_track_id = str(data.get("dz_track_id") or "")
    title = data.get("title") or ""
    artist = data.get("artist") or ""
    if not (dz_track_id or title):
        return jsonify(error="dz_track_id or title required"), 400

    meta = {"title": title, "artist": artist, "album": data.get("album") or "",
            "album_artist": artist, "duration_ms": data.get("duration_ms"),
            "cover_url": data.get("cover_url") or "", "isrc": ""}

    # Shared enrichment+enqueue: adopt a confident Spotify match's sid/ISRC when
    # connected, else the Deezer ISRC — same path the playlist "queue missing" action
    # uses (grabber/enqueue.py).
    item_id = enqueue_track(state().db, g, meta, dz_track_id=dz_track_id)
    if item_id is None:
        return jsonify(ok=False, error="already queued"), 409
    g.request_sync()
    sid = data.get("suggestion_id")
    if sid:
        state().db.mark_suggestion_queued(sid)
    return jsonify(ok=True, id=item_id)


@suggestions_bp.route("/api/suggestions/queue-album", methods=["POST"])
@login_required
def queue_album():
    """Enqueue a whole Deezer album/single — every track not already in the
    library or the queue. Bulk-friendly: no per-track Spotify/ISRC enrichment
    (the download worker matches providers by metadata); tracks are deduped
    within the request and against the current non-terminal queue."""
    _check_csrf()
    g = _grabber()
    if not g:
        return jsonify(error="grabber_disabled"), 409
    data = request.get_json(force=True, silent=True) or {}
    album_id = str(data.get("album_id") or "")
    if not album_id:
        return jsonify(error="album_id required"), 400
    alb = dz.album(album_id)
    if not alb:
        return jsonify(ok=False, error="not_found"), 404
    db = state().db
    queued_keys = _queued_norm_keys(db)
    enqueued = 0
    for t in alb["tracks"]:
        title, artist = t.get("title") or "", t.get("artist") or ""
        meta = {"title": title, "artist": artist, "album": t.get("album") or "",
                "album_artist": alb.get("artist") or artist,
                "duration_ms": t.get("duration_ms"), "cover_url": t.get("cover_url") or "",
                "isrc": ""}
        if library_match(meta, db):
            continue
        key = (normalize_title(title), normalize_artist(artist))
        if key in queued_keys:
            continue
        if db.enqueue_grab(meta) is not None:
            enqueued += 1
            queued_keys.add(key)   # avoid duplicating repeated titles within the album
    g.request_sync()
    return jsonify(ok=True, enqueued=enqueued, total=len(alb["tracks"]))


# ══════════════════════════════════════════════════════════════════════════
# Part B — Related panel (login-gated only; NOT grabber-gated)
# ══════════════════════════════════════════════════════════════════════════
_CACHE: dict[str, tuple[float, object]] = {}
_CACHE_TTL = 24 * 3600
_CACHE_MAX = 200
_cache_lock = threading.Lock()


def _cache_get(key: str):
    with _cache_lock:
        ent = _CACHE.get(key)
        if not ent:
            return None
        expires_at, payload = ent
        if expires_at < time.monotonic():
            _CACHE.pop(key, None)
            return None
        return payload


def _cache_put(key: str, payload: list) -> None:
    with _cache_lock:
        if len(_CACHE) >= _CACHE_MAX and key not in _CACHE:
            oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
            _CACHE.pop(oldest, None)
        _CACHE[key] = (time.monotonic() + _CACHE_TTL, payload)


@suggestions_bp.route("/api/related/artists")
@login_required
def related_artists():
    """Artists similar to ``name``, each tagged with library track_count (0 = not
    owned; when >0, library_name gives the library's display spelling to link to)."""
    name = request.args.get("name", "").strip()
    if not name:
        return jsonify(artists=[])
    ck = "artists:" + normalize_artist(name)
    payload = _cache_get(ck)
    if payload is None:
        hit = dz.search_artist(name)
        rels = dz.related_artists(hit["dz_id"]) if hit else []
        payload = [{"dz_id": r["dz_id"], "name": r["name"],
                    "image_url": r["image_url"]} for r in rels]
        _cache_put(ck, payload)
    lib = build_library_artists(state().db)
    out = []
    for r in payload:
        disp, count = lib.get(normalize_artist(r["name"]), ("", 0))
        entry = {"dz_id": r["dz_id"], "name": r["name"],
                 "image_url": r["image_url"], "track_count": count}
        if count > 0:
            entry["library_name"] = disp
        out.append(entry)
    return jsonify(artists=out)


@suggestions_bp.route("/api/related/tracks")
@login_required
def related_tracks():
    """Tracks in the style of ``name`` (Deezer artist radio), flagged in_library /
    queued per request."""
    name = request.args.get("name", "").strip()
    if not name:
        return jsonify(tracks=[])
    ck = "tracks:" + normalize_artist(name)
    payload = _cache_get(ck)
    if payload is None:
        hit = dz.search_artist(name)
        payload = dz.artist_radio(hit["dz_id"]) if hit else []
        _cache_put(ck, payload)
    tracks = [dict(t) for t in payload]  # copy: flags must not pollute the cache
    _flag_tracks(tracks, state().db)
    return jsonify(tracks=tracks)


@suggestions_bp.route("/api/related/description")
@login_required
def related_description():
    """A short, best-effort artist description (keyless MusicBrainz→Wikidata→
    Wikipedia). Empty string when none is found — never an error."""
    name = request.args.get("name", "").strip()
    if not name:
        return jsonify(description="")
    ck = "bio:" + normalize_artist(name)
    payload = _cache_get(ck)
    if payload is None:
        from ...integrations.artist_info import artist_bio
        payload = {"description": artist_bio(name)}
        _cache_put(ck, payload)
    return jsonify(payload)


@suggestions_bp.route("/api/deezer/resolve")
@login_required
def deezer_resolve():
    """Resolve a library artist name to its Deezer artist ({dz_id, name,
    image_url}) so the artist page can open the catalog browser. Login-gated,
    read-only. ``artist`` is null when Deezer has no match — never an error."""
    name = request.args.get("name", "").strip()
    if not name:
        return jsonify(artist=None)
    ck = "resolve:" + normalize_artist(name)
    payload = _cache_get(ck)
    if payload is None:
        payload = {"artist": dz.search_artist(name)}
        _cache_put(ck, payload)
    return jsonify(payload)


def _dedupe_albums(albums: list[dict]) -> list[dict]:
    """Collapse Deezer's frequent duplicate/regional releases by normalized
    title, keeping the first (newest) occurrence."""
    seen, out = set(), []
    for a in albums:
        key = (a.get("title") or "").strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out


@suggestions_bp.route("/api/deezer/artist/<dz_id>")
@login_required
def deezer_artist(dz_id):
    """An artist's profile, top tracks and discography (albums + singles/EPs)
    from Deezer. Login-gated, read-only — the add actions stay grabber-gated.
    Deezer payloads are cached; track flags are recomputed per request."""
    ck = "dzartist:" + str(dz_id)
    payload = _cache_get(ck)
    if payload is None:
        info = dz.get_artist(dz_id)
        if not info:
            return jsonify(error="not_found"), 404
        payload = {
            "artist": info,
            "top_tracks": dz.artist_top_tracks(dz_id, limit=10),
            "albums_all": _dedupe_albums(dz.artist_albums(dz_id, limit=100)),
        }
        _cache_put(ck, payload)
    top = [dict(t) for t in payload["top_tracks"]]
    _flag_tracks(top, state().db)
    albums, singles = [], []
    for a in payload["albums_all"]:
        (singles if a["record_type"] in ("single", "ep") else albums).append(a)
    return jsonify(artist=payload["artist"], top_tracks=top, albums=albums, singles=singles)


@suggestions_bp.route("/api/deezer/album/<album_id>")
@login_required
def deezer_album(album_id):
    """One album with its tracklist, each track flagged in_library / queued."""
    ck = "dzalbum:" + str(album_id)
    payload = _cache_get(ck)
    if payload is None:
        alb = dz.album(album_id)
        if not alb:
            return jsonify(error="not_found"), 404
        payload = alb
        _cache_put(ck, payload)
    alb = {**payload, "tracks": [dict(t) for t in payload["tracks"]]}
    _flag_tracks(alb["tracks"], state().db)
    return jsonify(album=alb)
