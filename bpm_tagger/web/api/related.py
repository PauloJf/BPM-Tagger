"""Related endpoints (Deezer-derived, keyless).

The Related panel on Artist/Album/Track pages: live similar-artists / similar-tracks
lookups, the offline "from your library" list, an artist bio, and the Deezer catalog
browser behind the artist page. All ``login_required`` only: read-only lookups that
help you navigate your own library.

A tiny in-memory TTL cache holds the raw Deezer payloads for the Related
endpoints so navigating between artists doesn't re-hit Deezer; the ``in_library``
flag is recomputed per request on top of the cached payload (the library changes,
the catalog doesn't).
"""

import logging
import os
import threading
import time

from flask import Blueprint, jsonify, request

from ...integrations import deezer_catalog as dz
from ...library_artists import build_library_artists
from ...matching import library_match, normalize_artist
from ..auth import _check_csrf, login_required
from ..state import state

log = logging.getLogger(__name__)

related_bp = Blueprint("api_related", __name__)


# ── shared flagging ─────────────────────────────────────────────────────────
def _flag_tracks(tracks: list[dict], db) -> list[dict]:
    """Add a live ``in_library`` flag (+ ``file_path``/``bpm`` when matched) to a list
    of track dicts (title/artist/album/duration_ms). Mutates in place. ``bpm`` lets the
    frontend enqueue a matched track straight into the play queue (the tempo lock
    stretches by BPM)."""
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
    return tracks


# ══════════════════════════════════════════════════════════════════════════
# Related panel
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


@related_bp.route("/api/related/artists")
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


@related_bp.route("/api/related/tracks")
@login_required
def related_tracks():
    """Tracks in the style of ``name`` (Deezer artist radio), flagged in_library per request."""
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


@related_bp.route("/api/related/library", methods=["GET", "POST"])
@login_required
def related_library():
    """Similar tracks from the library itself (offline, always playable): the
    seed's artists first, then tracks at a nearby tempo — the rule in
    ``web/similar.py``. Serves the Similar panel's "From your library" section
    (GET ?path=) and Listen's similar radio (POST, with the recently queued
    paths as ``exclude``).

    ``target`` + ``stretch_pct`` (a run's cadence and stretch limit) centre the
    tempo band on the cadence instead of the seed, so every result can play on
    cadence. A player user only gets tracks from its own playlists.

    Picking is rating-weighted for the caller's own account (owner + weights),
    and the caller's dislikes are dropped (docs/plans/ratings-weighted-picking.md)."""
    from ..auth import session_owner
    from ..similar import similar_tracks
    from ..weighting import weights_from_config
    from .run import _run_scope
    st = state()
    if request.method == "POST":
        _check_csrf()
        body = request.get_json(silent=True) or {}
    else:
        body = request.args
    path = str(body.get("path") or "")
    track = st.db.get_track(path) if path else None
    if not track or track.get("status") == "deleted":
        return jsonify(tracks=[])
    full, allowed = _run_scope()
    scope = None if full else sorted(allowed)
    try:
        count = max(1, min(100, int(body.get("count") or 20)))
    except (TypeError, ValueError):
        count = 20
    target = tolerance = None
    try:
        if body.get("target") not in (None, ""):
            target = float(body.get("target"))
            tolerance = max(0.01, float(body.get("stretch_pct") or 15.0) / 100.0)
    except (TypeError, ValueError):
        target = tolerance = None
    exclude = body.get("exclude") if request.method == "POST" else None
    exclude = [str(p) for p in (exclude or [])[:200]] if isinstance(exclude, list) else []
    owner = session_owner()
    weights = weights_from_config(st.config, owner)
    picked = similar_tracks(st.db, [track], count, scope, exclude, target, tolerance,
                           owner=owner, weights=weights)
    return jsonify(tracks=[{
        "path": t["file_path"],
        "title": t["title"] or os.path.splitext(os.path.basename(t["file_path"]))[0],
        "artist": t["artist"] or "",
        "bpm": t["bpm"],
        "starred": bool(t.get("starred")),
        "rating": t.get("rating"),
        "loudness_lufs": t["loudness_lufs"],
        "reason": reason,
    } for t, reason in picked])


@related_bp.route("/api/related/description")
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


@related_bp.route("/api/deezer/resolve")
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


@related_bp.route("/api/deezer/search-artists")
@login_required
def deezer_search_artists():
    """Deezer artists matching a free-text query, for the Search page's artist
    strip (each opens the catalog browser). Login-gated, read-only. Deezer hits
    are cached; ``track_count`` (+ ``library_name``) is per request."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify(artists=[])
    ck = "searchartists:" + q.casefold()
    payload = _cache_get(ck)
    if payload is None:
        payload = dz.search_artists(q, limit=6)
        _cache_put(ck, payload)
    lib = build_library_artists(state().db)
    out = []
    for a in payload:
        disp, count = lib.get(normalize_artist(a["name"]), ("", 0))
        entry = dict(a, track_count=count)
        if count > 0:
            entry["library_name"] = disp
        out.append(entry)
    return jsonify(artists=out)


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


@related_bp.route("/api/deezer/artist/<dz_id>")
@login_required
def deezer_artist(dz_id):
    """An artist's profile, top tracks and discography (albums + singles/EPs)
    from Deezer. Login-gated, read-only.
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


@related_bp.route("/api/deezer/album/<album_id>")
@login_required
def deezer_album(album_id):
    """One album with its tracklist, each track flagged in_library."""
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
