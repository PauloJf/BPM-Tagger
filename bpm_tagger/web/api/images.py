"""Image search (Spotify + Deezer) and artist / album / track image editing.

Search returns candidate image URLs from Deezer (keyless) and Spotify (when the
grabber is connected); apply endpoints accept either a candidate URL (fetched
server-side) or raw uploaded bytes. Album covers are embedded into every track
of the album; artist images land in the on-disk custom store that
``/api/artist/image`` serves first. Every file write refreshes the DB hash so
the watcher never re-analyzes an edited file.
"""

import ipaddress
import logging
import os
import socket
from urllib.parse import urlparse

import requests
from flask import Blueprint, jsonify, request

from ...bpm.tags import get_file_hash
from ...grabber.tagging import embed_cover, resize_cover
from ...integrations.ratelimit import deezer_limiter
from ..auth import _check_csrf, login_required
from ..state import _assert_in_music_dir, state
from .tracks import _artist_image_cache, _save_artist_image_to_library

log = logging.getLogger(__name__)

images_bp = Blueprint("api_images", __name__)

_MAX_IMAGE_BYTES = 15 * 1024 * 1024
_SEARCH_LIMIT = 8


def _is_public_host(url: str) -> bool:
    """True when every address the URL's host resolves to is publicly routable.

    Candidate image URLs come from the client, so without this check the apply
    endpoints would fetch attacker-chosen URLs from inside the network (SSRF
    against LAN services / cloud metadata). Cover-art CDNs are always public.
    """
    host = urlparse(url).hostname
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    try:
        return all(ipaddress.ip_address(info[4][0]).is_global for info in infos)
    except ValueError:
        return False


def _fetch_image(url: str) -> bytes | None:
    """Fetch a candidate image server-side. http(s) only; public hosts only;
    size-capped."""
    if not url or not url.lower().startswith(("http://", "https://")):
        return None
    if not _is_public_host(url):
        log.warning("Image fetch refused (non-public host): %s", url)
        return None
    try:
        resp = requests.get(url, timeout=15, stream=True)
        resp.raise_for_status()
        data = resp.raw.read(_MAX_IMAGE_BYTES + 1, decode_content=True)
        if not data or len(data) > _MAX_IMAGE_BYTES:
            return None
        return resize_cover(data)
    except Exception as exc:
        log.warning("Image fetch failed (%s): %s", url, exc)
        return None


def _image_from_request(data: dict | None) -> tuple[bytes | None, str | None]:
    """Resolve the image bytes for an apply call: JSON {url} → server-side fetch;
    otherwise the raw request body (file upload). Returns (bytes, error)."""
    if data and data.get("url"):
        img = _fetch_image(str(data["url"]))
        return (img, None) if img else (None, "could not fetch image from URL")
    raw = request.get_data()
    if raw:
        if len(raw) > _MAX_IMAGE_BYTES:
            return None, "image too large (max 15 MB)"
        return resize_cover(raw), None
    return None, "no image supplied (url or body required)"


# ── Search ────────────────────────────────────────────────────────────────────

def _deezer_search(kind: str, query: str) -> list[dict]:
    deezer_limiter.acquire()  # stay under Deezer's public 50 req / 5 s quota
    resp = requests.get(f"https://api.deezer.com/search/{kind}",
                        params={"q": query, "limit": _SEARCH_LIMIT}, timeout=8)
    resp.raise_for_status()
    return resp.json().get("data") or []


def _search_deezer(kind: str, query: str) -> list[dict]:
    out = []
    try:
        if kind == "artist":
            for a in _deezer_search("artist", query):
                url = a.get("picture_xl") or a.get("picture_big") or ""
                if url and "/artist//" not in url:  # Deezer placeholder → skip
                    out.append({"source": "deezer", "name": a.get("name", ""),
                                "detail": "", "image_url": url,
                                "thumb_url": a.get("picture_medium") or url})
        elif kind == "album":
            for al in _deezer_search("album", query):
                url = al.get("cover_xl") or al.get("cover_big") or ""
                if url:
                    out.append({"source": "deezer", "name": al.get("title", ""),
                                "detail": (al.get("artist") or {}).get("name", ""),
                                "image_url": url,
                                "thumb_url": al.get("cover_medium") or url})
        else:  # track → its album cover
            for t in _deezer_search("track", query):
                album = t.get("album") or {}
                url = album.get("cover_xl") or album.get("cover_big") or ""
                if url:
                    out.append({"source": "deezer", "name": t.get("title", ""),
                                "detail": (t.get("artist") or {}).get("name", ""),
                                "image_url": url,
                                "thumb_url": album.get("cover_medium") or url})
    except Exception as exc:
        log.warning("Deezer image search failed: %s", exc)
    return out


def _search_spotify(kind: str, query: str) -> list[dict]:
    st = state()
    g = getattr(st.tagger, "grabber", None) if st.tagger else None
    client = g.client if g else None
    if client is None or not client.is_connected():
        return []
    out = []
    try:
        if kind == "artist":
            for a in client.search_artists(query, limit=_SEARCH_LIMIT):
                out.append({"source": "spotify", "name": a["name"], "detail": "",
                            "image_url": a["image_url"], "thumb_url": a["thumb_url"]})
        elif kind == "album":
            for al in client.search_albums(query, limit=_SEARCH_LIMIT):
                out.append({"source": "spotify", "name": al["name"], "detail": al["artist"],
                            "image_url": al["image_url"], "thumb_url": al["thumb_url"]})
        else:
            for t in client.search_tracks(query, limit=_SEARCH_LIMIT):
                if t.get("cover_url"):
                    out.append({"source": "spotify", "name": t.get("title", ""),
                                "detail": t.get("artist", ""),
                                "image_url": t["cover_url"], "thumb_url": t["cover_url"]})
    except Exception as exc:
        log.warning("Spotify image search failed: %s", exc)
    return out


@images_bp.route("/api/images/search")
@login_required
def api_images_search():
    """Candidate images for the picker. kind=artist|album|track; q overrides the
    query built from artist/album/title params."""
    kind = request.args.get("kind", "album")
    if kind not in ("artist", "album", "track"):
        return jsonify(candidates=[], error="invalid kind"), 400
    q = request.args.get("q", "").strip()
    if not q:
        parts = {
            "artist": [request.args.get("artist", "")],
            "album": [request.args.get("album_artist", "") or request.args.get("artist", ""),
                      request.args.get("album", "")],
            "track": [request.args.get("artist", ""), request.args.get("title", "")],
        }[kind]
        q = " ".join(p.strip() for p in parts if p.strip())
    if not q:
        return jsonify(candidates=[])

    candidates = _search_spotify(kind, q) + _search_deezer(kind, q)
    # Dedupe (several Deezer tracks share one album cover).
    seen: set[str] = set()
    unique = [c for c in candidates
              if c["image_url"] not in seen and not seen.add(c["image_url"])]
    return jsonify(candidates=unique, query=q)


# ── Artist image ──────────────────────────────────────────────────────────────

@images_bp.route("/api/artist/image", methods=["POST", "PUT"])
@login_required
def api_artist_image_set():
    """Set a custom artist image — JSON {name, url} (POST) or raw bytes upload
    (PUT with ?name=). Stored in the app's artist-image cache; served ahead of
    local artist.jpg and auto-fetched images."""
    _check_csrf()
    data = request.get_json(silent=True) if request.is_json else None
    name = ((data or {}).get("name") or request.args.get("name", "")).strip()
    if not name:
        return jsonify(ok=False, error="name required"), 400
    image, err = _image_from_request(data)
    if not image:
        return jsonify(ok=False, error=err), 400
    cache_dir, custom, cached, miss = _artist_image_cache(name)
    os.makedirs(cache_dir, exist_ok=True)
    with open(custom, "wb") as f:
        f.write(image)
    # Stale auto-fetch artifacts are best-effort deletes: the custom image
    # outranks them anyway, and one may be mid-serve (Windows locks open files).
    for stale in (cached, miss):
        try:
            if os.path.isfile(stale):
                os.remove(stale)
        except OSError:
            pass
    # With artist_images_to_library on, also file the pick as artist.jpg in
    # the artist's folder so Navidrome (and any other player) sees it.
    library_path = _save_artist_image_to_library(state(), name, image)
    log.info("UI: set custom artist image for %s", name)
    return jsonify(ok=True, library_path=library_path)


@images_bp.route("/api/artist/image", methods=["DELETE"])
@login_required
def api_artist_image_delete():
    """Remove the custom/cached artist image so resolution falls back to local
    files (and, if enabled, a fresh online lookup)."""
    _check_csrf()
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or request.args.get("name", "")).strip()
    if not name:
        return jsonify(ok=False, error="name required"), 400
    _, custom, cached, miss = _artist_image_cache(name)
    removed, errors = 0, []
    for p in (custom, cached, miss):
        try:
            if os.path.isfile(p):
                os.remove(p)
                removed += 1
        except OSError as exc:
            errors.append(str(exc))
    if errors:
        return jsonify(ok=False, error="; ".join(errors)), 500
    return jsonify(ok=True, removed=removed)


# ── Album / track covers ──────────────────────────────────────────────────────

@images_bp.route("/api/album/cover", methods=["POST", "PUT"])
@login_required
def api_album_cover_set():
    """Embed a cover into every track of an album — JSON {album, album_artist,
    url} (POST) or raw bytes upload (PUT with ?album=&album_artist=)."""
    _check_csrf()
    st = state()
    data = request.get_json(silent=True) if request.is_json else None
    album = ((data or {}).get("album") or request.args.get("album", "")).strip()
    album_artist = ((data or {}).get("album_artist")
                    or request.args.get("album_artist", "")).strip()
    if not album:
        return jsonify(ok=False, error="album required"), 400
    tracks = st.db.get_album_tracks(album, album_artist or None)
    if not tracks:
        return jsonify(ok=False, error="album not found"), 404
    image, err = _image_from_request(data)
    if not image:
        return jsonify(ok=False, error=err), 400

    updated, failed = 0, []
    for t in tracks:
        path = t["file_path"]
        try:
            _assert_in_music_dir(path)
            if not os.path.isfile(path):
                failed.append(os.path.basename(path))
                continue
            warn = embed_cover(path, image)
            if warn:
                failed.append(os.path.basename(path))
                continue
            st.db.refresh_track_hash(path, get_file_hash(path))
            updated += 1
        except Exception as exc:
            log.warning("Album cover embed failed for %s: %s", path, exc)
            failed.append(os.path.basename(path))
    log.info("UI: album cover set for %s (%d updated, %d failed)",
             album, updated, len(failed))
    return jsonify(ok=updated > 0, updated=updated, failed=failed)


@images_bp.route("/api/track/cover", methods=["POST"])
@login_required
def api_track_cover_from_url():
    """Embed a cover fetched from a candidate URL into a single track. (Raw
    upload stays on PUT /api/track/cover.)"""
    _check_csrf()
    st = state()
    data = request.get_json(force=True, silent=True) or {}
    path = data.get("file_path", "")
    if not path:
        return jsonify(ok=False, error="file_path required"), 400
    _assert_in_music_dir(path)
    if not st.db.get_track(path):
        return jsonify(ok=False, error="not found"), 404
    image = _fetch_image(str(data.get("url") or ""))
    if not image:
        return jsonify(ok=False, error="could not fetch image from URL"), 400
    warn = embed_cover(path, image)
    if warn:
        return jsonify(ok=False, error=warn), 500
    st.db.refresh_track_hash(path, get_file_hash(path))
    return jsonify(ok=True)
