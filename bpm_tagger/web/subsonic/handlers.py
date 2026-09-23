"""Subsonic method handlers (Phase 1 — docs/plans/subsonic-api.md).

Each handler takes the AppState and returns a Flask Response. Parameters come
from ``request.values`` (query string or form body — OpenSubsonic formPost).
"""

import io
import logging
import os
import threading
import time
from datetime import datetime, timezone

from flask import Response, request, send_file

from ..state import _assert_in_music_dir
from . import ids, views
from .envelope import E_MISSING_PARAM, E_NOT_FOUND, SubsonicError, ok

log = logging.getLogger(__name__)

albums_index = ids.AlbumIndex()

_FOLDER_COVERS = ("cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "folder.jpeg",
                  "folder.png", "front.jpg", "front.png")


# ── Parameter helpers ─────────────────────────────────────────────────────────

def _req(name: str) -> str:
    val = request.values.get(name)
    if val is None or val == "":
        raise SubsonicError(E_MISSING_PARAM, f"Required parameter is missing: {name}")
    return val


def _int(name: str, default: int, lo: int = 0, hi: int = 10_000) -> int:
    try:
        return max(lo, min(hi, int(request.values.get(name, default))))
    except (TypeError, ValueError):
        return default


def _opt_int(name: str):
    try:
        v = request.values.get(name)
        return int(v) if v not in (None, "") else None
    except ValueError:
        return None


def _query() -> str:
    # Some clients send the empty "sync everything" query as a literal "".
    q = (request.values.get("query") or "").strip()
    return "" if q in ('""', "''", "*") else q.strip('"')


def _song_or_404(st, sid: str) -> dict:
    tid = ids.parse_song_id(sid)
    track = st.db.get_track_by_id(tid) if tid is not None else None
    if not track:
        raise SubsonicError(E_NOT_FOUND, "Song not found.")
    return track


# ── Artist lookup (cached like the album index) ──────────────────────────────

class _Artists:
    def __init__(self):
        self._lock = threading.Lock()
        self._at = 0.0
        self._rows: list[dict] = []

    def all(self, db) -> list[dict]:
        with self._lock:
            if time.monotonic() - self._at > 10.0:
                self._rows, self._at = db.list_artists(), time.monotonic()
            return self._rows

    def by_id(self, db, aid: str):
        for row in self.all(db):
            if ids.artist_id(row["name"]) == aid:
                return row
        return None


artists_index = _Artists()


# ── System ────────────────────────────────────────────────────────────────────

def ping(st):
    return ok()


def get_license(st):
    return ok(license={"valid": True})


def get_open_subsonic_extensions(st):
    return ok(openSubsonicExtensions=[
        {"name": "apiKeyAuthentication", "versions": [1]},
        {"name": "formPost", "versions": [1]},
    ])


def get_music_folders(st):
    return ok(musicFolders={"musicFolder": [{"id": 1, "name": "Music"}]})


def get_user(st):
    from .auth import subsonic_username
    name = request.values.get("username") or subsonic_username()
    if name.lower() != subsonic_username().lower():
        raise SubsonicError(E_NOT_FOUND, "User not found.")
    return ok(user={
        "username": subsonic_username(), "email": "", "scrobblingEnabled": True,
        "adminRole": True, "settingsRole": False, "downloadRole": True,
        "uploadRole": False, "playlistRole": False, "coverArtRole": True,
        "commentRole": False, "podcastRole": False, "streamRole": True,
        "jukeboxRole": False, "shareRole": False, "videoConversionRole": False,
        "folder": [1],
    })


def get_scan_status(st):
    progress = getattr(st, "progress", None)
    scanning = bool(getattr(progress, "is_scanning", False)) if progress else False
    return ok(scanStatus={"scanning": scanning, "count": st.db.count_live_tracks()})


# ── Browsing (ID3) ────────────────────────────────────────────────────────────

def get_artists(st):
    buckets: dict[str, list[dict]] = {}
    for row in sorted(artists_index.all(st.db), key=lambda r: views.sort_name(r["name"])):
        buckets.setdefault(views.index_letter(row["name"]), []).append(views.artist(row))
    index = [{"name": k, "artist": buckets[k]}
             for k in sorted(buckets, key=lambda k: (k == "#", k))]
    return ok(artists={"ignoredArticles": views.IGNORED_ARTICLES, "index": index})


def get_artist(st):
    aid = _req("id")
    row = artists_index.by_id(st.db, aid)
    if not row:
        raise SubsonicError(E_NOT_FOUND, "Artist not found.")
    tracks = st.db.get_artist_tracks(row["name"])
    keys = sorted({(t["album"], t.get("album_artist") or "") for t in tracks if t.get("album")})
    albums = [views.album(a) for a in st.db.subsonic_albums(
        "byYear", limit=10_000, keys=keys)] if keys else []
    return ok(artist={**views.artist(row), "albumCount": len(albums), "album": albums})


def get_album(st):
    aid = _req("id")
    keys = albums_index.keys_for(st.db, aid)
    if not keys:
        raise SubsonicError(E_NOT_FOUND, "Album not found.")
    rows = st.db.subsonic_albums("alphabeticalByName", limit=len(keys), keys=keys)
    if not rows:
        raise SubsonicError(E_NOT_FOUND, "Album not found.")
    base = views.album(rows[0])
    if len(rows) > 1:  # case/accent variants merged under one id
        base["songCount"] = sum(r["song_count"] for r in rows)
        base["duration"] = sum(int((r["duration_ms"] or 0) / 1000) for r in rows)
    songs = [views.song(t, st.music_dir) for t in st.db.subsonic_album_tracks(keys)]
    return ok(album={**base, "song": songs})


def get_song(st):
    return ok(song=views.song(_song_or_404(st, _req("id")), st.music_dir))


def _album_list(st):
    kind = _req("type")
    if kind == "byGenre":
        return []  # genres aren't indexed (yet)
    size = _int("size", 10, 1, 500)
    offset = _int("offset", 0, 0, 1_000_000)
    rows = st.db.subsonic_albums(kind, limit=size, offset=offset,
                                 from_year=_opt_int("fromYear"), to_year=_opt_int("toYear"))
    return [views.album(r) for r in rows]


def get_album_list2(st):
    return ok(albumList2={"album": _album_list(st)})


def get_album_list(st):
    return ok(albumList={"album": _album_list(st)})


def get_genres(st):
    return ok(genres={"genre": []})


def get_random_songs(st):
    rows = st.db.subsonic_random_songs(_int("size", 10, 1, 500),
                                       _opt_int("fromYear"), _opt_int("toYear"))
    return ok(randomSongs={"song": [views.song(t, st.music_dir) for t in rows]})


def _search(st):
    q = _query()
    songs = st.db.subsonic_search_songs(q, _int("songCount", 20, 0, 1000),
                                        _int("songOffset", 0, 0, 10_000_000))
    album_keys = st.db.subsonic_search_album_keys(q, _int("albumCount", 20, 0, 1000),
                                                  _int("albumOffset", 0, 0, 10_000_000))
    albums = st.db.subsonic_albums("alphabeticalByName", limit=len(album_keys),
                                   keys=album_keys) if album_keys else []
    ql = q.lower()
    all_artists = [a for a in artists_index.all(st.db) if not ql or ql in a["name"].lower()]
    a_off = _int("artistOffset", 0, 0, 10_000_000)
    artists = all_artists[a_off:a_off + _int("artistCount", 20, 0, 1000)]
    return {
        "artist": [views.artist(a) for a in artists],
        "album": [views.album(a) for a in albums],
        "song": [views.song(t, st.music_dir) for t in songs],
    }


def search3(st):
    return ok(searchResult3=_search(st))


def search2(st):
    return ok(searchResult2=_search(st))


def get_playlists(st):
    return ok(playlists={"playlist": []})  # Phase 2


def get_now_playing(st):
    return ok(nowPlaying={"entry": []})


# ── Media ─────────────────────────────────────────────────────────────────────

def stream(st, as_attachment: bool = False):
    track = _song_or_404(st, _req("id"))
    real = _assert_in_music_dir(track["file_path"])
    if not os.path.isfile(real):
        raise SubsonicError(E_NOT_FOUND, "File is missing on disk.")
    ext = os.path.splitext(real)[1].lower()
    return send_file(real, mimetype=views.CONTENT_TYPES.get(ext, "application/octet-stream"),
                     conditional=True, as_attachment=as_attachment,
                     download_name=os.path.basename(real))


def download(st):
    return stream(st, as_attachment=True)


def _folder_cover(track_path: str):
    d = os.path.dirname(track_path)
    for name in _FOLDER_COVERS:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            with open(p, "rb") as f:
                return f.read(), "image/png" if name.endswith(".png") else "image/jpeg"
    return None


def _cover_for_track(track: dict):
    from ...grabber.tagging import read_cover
    path = _assert_in_music_dir(track["file_path"])
    return read_cover(path) or _folder_cover(path)


def _resize(data: bytes, mime: str, size):
    if not size:
        return data, mime
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        if max(img.size) <= size:
            return data, mime
        img = img.convert("RGB")
        img.thumbnail((size, size))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=88)
        return out.getvalue(), "image/jpeg"
    except Exception:  # Pillow missing or unreadable image → original bytes
        return data, mime


def get_cover_art(st):
    cid = _req("id")
    candidates: list[dict] = []
    if cid.startswith("tr-"):
        candidates = [_song_or_404(st, cid)]
    elif cid.startswith("al-"):
        candidates = st.db.subsonic_album_tracks(albums_index.keys_for(st.db, cid))[:8]
    elif cid.startswith("ar-"):
        row = artists_index.by_id(st.db, cid)
        candidates = st.db.get_artist_tracks(row["name"])[:8] if row else []
    for track in candidates:
        cover = _cover_for_track(track)
        if cover:
            data, mime = cover
            if not (mime or "").lower().startswith("image/"):
                mime = "application/octet-stream"
            data, mime = _resize(data, mime, _opt_int("size"))
            return Response(data, mimetype=mime,
                            headers={"Cache-Control": "private, max-age=86400"})
    # Spec: 404 with no body is what clients expect for "no art".
    return Response(status=404)


# ── Annotation ────────────────────────────────────────────────────────────────

def _song_ids() -> list[str]:
    return [i for i in request.values.getlist("id") if i]


def _set_star(st, starred: bool):
    # Album/artist stars (albumId / artistId) have nowhere to live yet — accepted
    # and ignored, so clients don't surface an error. Song stars are real stars:
    # the same flag the web UI, Run mode and Navidrome star sync use.
    for sid in _song_ids():
        track = _song_or_404(st, sid)
        st.db.set_starred(track["file_path"], starred)
    return ok()


def star(st):
    return _set_star(st, True)


def unstar(st):
    return _set_star(st, False)


def get_starred2(st):
    return ok(starred2={"artist": [], "album": [],
                        "song": [views.song(t, st.music_dir) for t in st.db.subsonic_starred_songs()]})


def get_starred(st):
    return ok(starred={"artist": [], "album": [],
                       "song": [views.song(t, st.music_dir) for t in st.db.subsonic_starred_songs()]})


def scrobble(st, owner: str):
    if request.values.get("submission", "true").lower() == "false":
        return ok()  # "now playing" — nothing to record
    times = request.values.getlist("time")
    for i, sid in enumerate(_song_ids()):
        track = _song_or_404(st, sid)
        path = track["file_path"]
        st.db.bump_play_count(path)
        played_at = None
        try:
            if i < len(times):
                played_at = datetime.fromtimestamp(int(times[i]) / 1000, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            played_at = None
        try:
            st.db.record_play_event(owner, path, duration_ms=track.get("duration_ms"),
                                    now=played_at)
        except Exception as exc:  # pragma: no cover - attribution is best effort
            log.debug("Subsonic play-event attribution failed: %s", exc)
        from ..api.media import forward_scrobble
        try:
            forward_scrobble(st, track)
        except Exception as exc:  # pragma: no cover - forwarding is best effort
            log.debug("Subsonic scrobble forward failed: %s", exc)
    return ok()


# method name → (handler, needs_owner)
METHODS = {
    "ping": ping, "getLicense": get_license,
    "getOpenSubsonicExtensions": get_open_subsonic_extensions,
    "getMusicFolders": get_music_folders, "getUser": get_user,
    "getScanStatus": get_scan_status,
    "getArtists": get_artists, "getArtist": get_artist, "getAlbum": get_album,
    "getSong": get_song, "getAlbumList2": get_album_list2, "getAlbumList": get_album_list,
    "getGenres": get_genres, "getRandomSongs": get_random_songs,
    "search3": search3, "search2": search2,
    "getPlaylists": get_playlists, "getNowPlaying": get_now_playing,
    "stream": stream, "download": download, "getCoverArt": get_cover_art,
    "star": star, "unstar": unstar, "getStarred2": get_starred2, "getStarred": get_starred,
    "scrobble": scrobble,
}
OWNER_METHODS = {"scrobble"}
