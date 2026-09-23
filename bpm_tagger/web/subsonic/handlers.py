"""Subsonic method handlers (docs/plans/subsonic-api.md, Phases 1–2).

Every handler takes ``(st, who)`` — the AppState and the authenticated
``Principal`` — and returns a Flask Response. ``who.scope`` is threaded into
every library query, so a player user never sees (or streams, or stars) a track
outside its playlists. Parameters come from ``request.values`` (query string or
form body — OpenSubsonic formPost).
"""

import io
import logging
import os
import random
import re
import statistics
import threading
import time
from datetime import datetime, timezone

from flask import Response, request, send_file

from ...text import normalize_artist_name, split_artist_credits
from ..state import _assert_in_music_dir
from . import ids, views
from .dirs import dir_index
from . import transcode
from .envelope import E_GENERIC, E_MISSING_PARAM, E_NOT_AUTHORIZED, E_NOT_FOUND, SubsonicError, ok

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


def _song_or_404(st, who, sid: str) -> dict:
    tid = ids.parse_song_id(sid)
    track = st.db.get_track_by_id(tid, who.scope) if tid is not None else None
    if not track:
        raise SubsonicError(E_NOT_FOUND, "Song not found.")
    return track


def _songs(st, rows) -> list:
    return [views.song(t, st.music_dir) for t in rows]


def _require_admin(who, message: str = "This account can't change playlists."):
    if not who.is_admin:
        raise SubsonicError(E_NOT_AUTHORIZED, message)


# ── Artist lookup (cached per scope, like the album index) ───────────────────

class _Artists:
    def __init__(self):
        self._lock = threading.Lock()
        self._cache: dict = {}

    def all(self, db, scope) -> list[dict]:
        key = (db.db_path, None if scope is None else tuple(scope))
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < 10.0:
                return hit[1]
        rows = db.subsonic_artists(scope)
        with self._lock:
            if len(self._cache) > 32:
                self._cache.clear()
            self._cache[key] = (time.monotonic(), rows)
        return rows

    def by_id(self, db, scope, aid: str):
        for row in self.all(db, scope):
            if ids.artist_id_norm(row["norm_name"]) == aid:
                return row
        return None


artists_index = _Artists()


def _artist_or_404(st, who, aid: str) -> dict:
    row = artists_index.by_id(st.db, who.scope, aid)
    if not row:
        raise SubsonicError(E_NOT_FOUND, "Artist not found.")
    return row


def _stars(st, kind: str) -> dict:
    return st.db.subsonic_star_map(kind)


def _album_keys(tracks) -> list:
    return sorted({(t["album"], t.get("album_artist") or "") for t in tracks if t.get("album")})


# ── System ────────────────────────────────────────────────────────────────────

def ping(st, who):
    return ok()


def get_license(st, who):
    return ok(license={"valid": True})


def get_open_subsonic_extensions(st, who):
    return ok(openSubsonicExtensions=[
        {"name": "apiKeyAuthentication", "versions": [1]},
        {"name": "formPost", "versions": [1]},
        {"name": "songLyrics", "versions": [1]},
    ])


def get_music_folders(st, who):
    return ok(musicFolders={"musicFolder": [{"id": 1, "name": "Music"}]})


def get_user(st, who):
    name = request.values.get("username") or who.username
    if name.lower() != who.username.lower():
        raise SubsonicError(E_NOT_AUTHORIZED, "You can only look up your own user.")
    return ok(user={
        "username": who.username, "email": "", "scrobblingEnabled": True,
        "adminRole": who.is_admin, "settingsRole": False, "downloadRole": True,
        "uploadRole": False, "playlistRole": who.is_admin, "coverArtRole": True,
        "commentRole": False, "podcastRole": False, "streamRole": True,
        "jukeboxRole": False, "shareRole": False, "videoConversionRole": False,
        "folder": [1],
    })


def _scan_status(st, who) -> dict:
    progress = getattr(st, "progress", None)
    scanning = bool(getattr(progress, "is_scanning", False)) if progress else False
    return {"scanning": scanning, "count": st.db.subsonic_count_tracks(who.scope)}


def get_scan_status(st, who):
    return ok(scanStatus=_scan_status(st, who))


def start_scan(st, who):
    """Start an incremental BPM scan (new/changed files), the same pass as the
    web UI's Scan button. ``fullScan=true`` is honoured as a forced re-analysis.
    Admin only. Already scanning: just report the status."""
    _require_admin(who, "Only the admin can start a scan.")
    tagger = getattr(st, "tagger", None)
    if tagger is None:
        raise SubsonicError(E_GENERIC, "Scanning isn't available in this process.")
    if not _scan_status(st, who)["scanning"]:
        force = (request.values.get("fullScan") or "").lower() == "true"
        threading.Thread(target=lambda: tagger.scan_directory(force=force),
                         name="subsonic-scan", daemon=True).start()
    status = _scan_status(st, who)
    status["scanning"] = True
    return ok(scanStatus=status)


# ── Browsing (ID3) ────────────────────────────────────────────────────────────

def _artist_index(st, who) -> list:
    buckets: dict[str, list[dict]] = {}
    stars = _stars(st, "artist")
    for row in sorted(artists_index.all(st.db, who.scope), key=lambda r: views.sort_name(r["name"])):
        buckets.setdefault(views.index_letter(row["name"]), []).append(views.artist(row, stars))
    return [{"name": k, "artist": buckets[k]}
            for k in sorted(buckets, key=lambda k: (k == "#", k))]


def get_artists(st, who):
    return ok(artists={"ignoredArticles": views.IGNORED_ARTICLES, "index": _artist_index(st, who)})


def _artist_albums(st, who, row) -> list:
    keys = _album_keys(st.db.subsonic_artist_tracks(row["norm_name"], who.scope))
    if not keys:
        return []
    stars = _stars(st, "album")
    return [views.album(a, stars) for a in st.db.subsonic_albums(
        "byYear", limit=10_000, keys=keys, scope=who.scope)]


def get_artist(st, who):
    row = _artist_or_404(st, who, _req("id"))
    albums = _artist_albums(st, who, row)
    return ok(artist={**views.artist(row, _stars(st, "artist")), "albumCount": len(albums),
                      "album": albums})


def _album(st, who, aid: str):
    keys = albums_index.keys_for(st.db, aid)
    rows = st.db.subsonic_albums("alphabeticalByName", limit=len(keys), keys=keys,
                                 scope=who.scope) if keys else []
    if not rows:
        raise SubsonicError(E_NOT_FOUND, "Album not found.")
    base = views.album(rows[0], _stars(st, "album"))
    if len(rows) > 1:  # case/accent variants merged under one id
        base["songCount"] = sum(r["song_count"] for r in rows)
        base["duration"] = sum(int((r["duration_ms"] or 0) / 1000) for r in rows)
    return base, st.db.subsonic_album_tracks(keys, who.scope)


def get_album(st, who):
    base, tracks = _album(st, who, _req("id"))
    return ok(album={**base, "song": _songs(st, tracks)})


def get_song(st, who):
    return ok(song=views.song(_song_or_404(st, who, _req("id")), st.music_dir))


def _album_list(st, who):
    kind = _req("type")
    size, offset = _int("size", 10, 1, 500), _int("offset", 0, 0, 1_000_000)
    stars = _stars(st, "album")
    if kind in ("starred", "byGenre"):
        # Both are a key set first, then the ordinary (name-ordered) album query.
        if kind == "starred":
            keys = [k for aid in stars for k in albums_index.keys_for(st.db, aid)]
        else:
            keys = st.db.subsonic_genre_album_keys(_req("genre"), who.scope)
        if not keys:
            return []
        rows = st.db.subsonic_albums("alphabeticalByName", limit=size, offset=offset,
                                     keys=keys, scope=who.scope)
    else:
        rows = st.db.subsonic_albums(kind, limit=size, offset=offset,
                                     from_year=_opt_int("fromYear"), to_year=_opt_int("toYear"),
                                     scope=who.scope)
    return [views.album(r, stars) for r in rows]


def get_album_list2(st, who):
    return ok(albumList2={"album": _album_list(st, who)})


def get_album_list(st, who):
    return ok(albumList={"album": _album_list(st, who)})


def get_genres(st, who):
    return ok(genres={"genre": [
        {"value": g["name"], "songCount": g["song_count"], "albumCount": g["album_count"]}
        for g in st.db.subsonic_genres(who.scope)]})


def get_songs_by_genre(st, who):
    rows = st.db.subsonic_genre_songs(_req("genre"), _int("count", 10, 1, 500),
                                      _int("offset", 0, 0, 10_000_000), who.scope)
    return ok(songsByGenre={"song": _songs(st, rows)})


def get_random_songs(st, who):
    rows = st.db.subsonic_random_songs(_int("size", 10, 1, 500), _opt_int("fromYear"),
                                       _opt_int("toYear"), who.scope,
                                       genre=request.values.get("genre") or None)
    return ok(randomSongs={"song": _songs(st, rows)})


def _search(st, who):
    q = _query()
    songs = st.db.subsonic_search_songs(q, _int("songCount", 20, 0, 1000),
                                        _int("songOffset", 0, 0, 10_000_000), who.scope)
    album_keys = st.db.subsonic_search_album_keys(q, _int("albumCount", 20, 0, 1000),
                                                  _int("albumOffset", 0, 0, 10_000_000), who.scope)
    albums = st.db.subsonic_albums("alphabeticalByName", limit=len(album_keys), keys=album_keys,
                                   scope=who.scope) if album_keys else []
    ql = q.lower()
    matches = [a for a in artists_index.all(st.db, who.scope) if not ql or ql in a["name"].lower()]
    a_off = _int("artistOffset", 0, 0, 10_000_000)
    album_stars, artist_stars = _stars(st, "album"), _stars(st, "artist")
    return {
        "artist": [views.artist(a, artist_stars)
                   for a in matches[a_off:a_off + _int("artistCount", 20, 0, 1000)]],
        "album": [views.album(a, album_stars) for a in albums],
        "song": _songs(st, songs),
    }


def search3(st, who):
    return ok(searchResult3=_search(st, who))


def search2(st, who):
    return ok(searchResult2=_search(st, who))


def get_now_playing(st, who):
    return ok(nowPlaying={"entry": []})


def get_artist_info2(st, who):
    _artist_or_404(st, who, _req("id"))
    return ok(artistInfo2={"similarArtist": []})


def get_artist_info(st, who):
    _artist_or_404(st, who, _req("id"))
    return ok(artistInfo={"similarArtist": []})


def get_album_info2(st, who):
    _album(st, who, _req("id"))
    return ok(albumInfo={})


# ── Browsing (folders) ────────────────────────────────────────────────────────

def get_indexes(st, who):
    root = dir_index.root(st.db, st.music_dir, who.scope)
    buckets: dict[str, list[dict]] = {}
    for rel in sorted(root.subdirs, key=views.sort_name):
        name = rel.rsplit("/", 1)[-1]
        buckets.setdefault(views.index_letter(name), []).append({"id": ids.dir_id(rel), "name": name})
    index = [{"name": k, "artist": buckets[k]} for k in sorted(buckets, key=lambda k: (k == "#", k))]
    files = st.db.subsonic_tracks_by_paths(sorted(root.files))
    return ok(indexes={
        "lastModified": int(time.time() * 1000), "ignoredArticles": views.IGNORED_ARTICLES,
        "index": index, "child": _songs(st, [files[p] for p in sorted(root.files) if p in files]),
    })


def get_music_directory(st, who):
    did = _req("id")
    if did.startswith("al-"):
        base, tracks = _album(st, who, did)
        return ok(directory={"id": did, "name": base["name"], "child": _songs(st, tracks)})
    if did.startswith("ar-"):
        row = _artist_or_404(st, who, did)
        return ok(directory={"id": did, "name": row["name"], "child": _artist_albums(st, who, row)})
    folder = (dir_index.root(st.db, st.music_dir, who.scope) if did == "1"
              else dir_index.get(st.db, st.music_dir, who.scope, did))
    if folder is None:
        raise SubsonicError(E_NOT_FOUND, "Directory not found.")
    subdirs = [views.directory_child(rel, folder.rel)
               for rel in sorted(folder.subdirs, key=lambda r: r.lower())]
    tracks = st.db.subsonic_tracks_by_paths(folder.files)
    songs = sorted((tracks[p] for p in folder.files if p in tracks),
                   key=lambda t: (t.get("disc_no") or 0, t.get("track_no") or 0, t["file_path"]))
    out = {"id": ids.dir_id(folder.rel), "name": folder.name or "Music",
           "child": subdirs + _songs(st, songs)}
    if folder.parent_rel is not None:
        out["parent"] = ids.dir_id(folder.parent_rel)
    return ok(directory=out)


# ── Playlists ─────────────────────────────────────────────────────────────────

# Run presets as virtual playlists (Phase 3): each preset's cadence as a playlist a
# Subsonic app can play. Apps can't tempo-lock, so tracks play at native speed,
# hence a tight band rather than Run mode's stretch limit: 4 %, octave-folded.
RUN_NATIVE_TOLERANCE = 0.04
RUN_PLAYLIST_MAX = 200
_RUN_PREFIX = "pl-run-"


def _run_presets(st) -> list:
    if not st.config.get("subsonic_run_playlists", True):
        return []
    from ..api.run import _presets
    return _presets(st.config)


def _run_playlist_tracks(st, who, preset: dict) -> list:
    from ..api.run import _eligible, _run_settings
    octave, _limit = _run_settings(st.config)
    cands = (st.db.get_run_candidates(None) if who.is_admin
             else st.db.get_run_candidates_for_playlists(who.scope or []))
    found = _eligible(cands, float(preset["bpm"]), octave, RUN_NATIVE_TOLERANCE)
    found.sort(key=lambda x: (not x[0]["starred"], x[2]))
    paths = [t["file_path"] for t, _f, _d in found[:RUN_PLAYLIST_MAX]]
    rows = st.db.subsonic_tracks_by_paths(paths)
    return [rows[p] for p in paths if p in rows]


def _run_playlist_view(st, who, index: int, preset: dict, tracks=None) -> dict:
    if tracks is None:
        tracks = _run_playlist_tracks(st, who, preset)
    pid = f"{_RUN_PREFIX}{index}"
    return {
        "id": pid, "name": f"Run · {preset['name']} ({preset['bpm']} BPM)",
        "comment": f"Tracks within {int(RUN_NATIVE_TOLERANCE * 100)} % of {preset['bpm']} BPM "
                   "(half/double time included), starred first. Plays at native speed.",
        "owner": who.username, "public": False, "songCount": len(tracks),
        "duration": sum(int((t.get("duration_ms") or 0) / 1000) for t in tracks),
        "created": "1970-01-01T00:00:00.000Z",
        "changed": views.iso(datetime.now(timezone.utc).isoformat()),
        "coverArt": pid, "readonly": True,
    }


def _run_preset_for(st, raw: str):
    if not raw.startswith(_RUN_PREFIX) or not raw[len(_RUN_PREFIX):].isdigit():
        return None
    index = int(raw[len(_RUN_PREFIX):])
    presets = _run_presets(st)
    return (index, presets[index]) if index < len(presets) else None


def _visible_playlists(st, who) -> list:
    return st.db.subsonic_playlist_summaries(None if who.is_admin else who.scope)


def _playlist_or_404(st, who, raw: str) -> dict:
    if raw.startswith(_RUN_PREFIX):
        # Virtual and read-only; callers that write reject it via _local_or_denied.
        if _run_preset_for(st, raw) is None:
            raise SubsonicError(E_NOT_FOUND, "Playlist not found.")
        return {"id": raw, "source": "run"}
    pid = ids.parse_playlist_id(raw)
    rows = st.db.subsonic_playlist_summaries([pid]) if pid is not None else []
    if not rows or (not who.is_admin and pid not in (who.scope or [])):
        raise SubsonicError(E_NOT_FOUND, "Playlist not found.")
    return rows[0]


def _playlist_view(st, who, row) -> dict:
    return views.playlist(row, who.username, who.is_admin and row.get("source") == "local")


def _playlist_with_entries(st, who, row) -> dict:
    fresh = st.db.subsonic_playlist_summaries([row["id"]])[0]
    return {**_playlist_view(st, who, fresh),
            "entry": _songs(st, st.db.subsonic_playlist_entries(row["id"]))}


def get_playlists(st, who):
    real = [_playlist_view(st, who, p) for p in _visible_playlists(st, who)]
    run = [_run_playlist_view(st, who, i, p) for i, p in enumerate(_run_presets(st))]
    return ok(playlists={"playlist": real + run})


def get_playlist(st, who):
    raw = _req("id")
    preset = _run_preset_for(st, raw)
    if preset is not None:
        index, p = preset
        tracks = _run_playlist_tracks(st, who, p)
        return ok(playlist={**_run_playlist_view(st, who, index, p, tracks),
                            "entry": _songs(st, tracks)})
    return ok(playlist=_playlist_with_entries(st, who, _playlist_or_404(st, who, raw)))


def _local_or_denied(row) -> None:
    if row.get("source") != "local":
        raise SubsonicError(E_NOT_AUTHORIZED,
                            "Only Local playlists can be changed here; synced playlists "
                            "belong to Spotify or Navidrome.")


def _paths_for(st, who, song_ids) -> list:
    return [_song_or_404(st, who, sid)["file_path"] for sid in song_ids]


def create_playlist(st, who):
    """New Local playlist (``name``), or — per spec — replace the songs of an
    existing one (``playlistId``). Library tracks are unique within a Local
    playlist, so a repeated song id is added once."""
    _require_admin(who)
    song_ids = [i for i in request.values.getlist("songId") if i]
    paths = _paths_for(st, who, song_ids)
    raw_pid = request.values.get("playlistId")
    if raw_pid:
        row = _playlist_or_404(st, who, raw_pid)
        _local_or_denied(row)
        for entry in st.db.subsonic_playlist_entries(row["id"]):
            st.db.remove_playlist_track(entry["pt_id"])
        pid = row["id"]
    else:
        pid = st.db.add_local_playlist(_req("name").strip()[:200])
    if paths:
        st.db.add_tracks_to_local_playlist(pid, paths)
    return ok(playlist=_playlist_with_entries(st, who, {"id": pid}))


def update_playlist(st, who):
    _require_admin(who)
    row = _playlist_or_404(st, who, _req("playlistId"))
    _local_or_denied(row)
    name = request.values.get("name")
    comment = request.values.get("comment")
    if name is not None or comment is not None:
        st.db.update_playlist_meta(row["id"], name=name.strip()[:200] if name else None,
                                   description=comment)
    entries = st.db.subsonic_playlist_entries(row["id"])
    remove = set()
    for raw in request.values.getlist("songIndexToRemove"):
        if raw.isdigit() and int(raw) < len(entries):
            remove.add(entries[int(raw)]["pt_id"])
    for pt_id in remove:
        st.db.remove_playlist_track(pt_id)
    add = _paths_for(st, who, [i for i in request.values.getlist("songIdToAdd") if i])
    if add:
        st.db.add_tracks_to_local_playlist(row["id"], add)
    return ok()


def delete_playlist(st, who):
    _require_admin(who)
    row = _playlist_or_404(st, who, _req("id"))
    _local_or_denied(row)
    st.db.delete_playlist(row["id"])
    return ok()


# ── Lyrics ────────────────────────────────────────────────────────────────────

_LRC_TIME = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_LRC_META = re.compile(r"^\[[a-zA-Z]+:.*\]$")


def _structured(text: str, synced: bool) -> list:
    lines = []
    for raw in text.splitlines():
        stamps = list(_LRC_TIME.finditer(raw))
        body = _LRC_TIME.sub("", raw).strip()
        if synced:
            if _LRC_META.match(raw.strip()) and not stamps:
                continue  # [ar:…] [ti:…] tags
            for m in stamps:
                frac = (m.group(3) or "0").ljust(3, "0")[:3]
                start = (int(m.group(1)) * 60 + int(m.group(2))) * 1000 + int(frac)
                lines.append({"start": start, "value": body})
        elif body or raw.strip() == "":
            lines.append({"value": body})
    if synced:
        lines.sort(key=lambda ln: ln["start"])
    return lines


def _read_lyrics(track):
    from ...bpm.lyrics import is_synced, read_lyrics
    found = read_lyrics(_assert_in_music_dir(track["file_path"]))
    if not found or not (found[0] or "").strip():
        return None
    return found[0], is_synced(found[0])


def get_lyrics_by_song_id(st, who):
    track = _song_or_404(st, who, _req("id"))
    found = _read_lyrics(track)
    if not found:
        return ok(lyricsList={"structuredLyrics": []})
    text, synced = found
    return ok(lyricsList={"structuredLyrics": [{
        "displayArtist": track.get("artist") or "", "displayTitle": track.get("title") or "",
        "lang": "und", "offset": 0, "synced": synced, "line": _structured(text, synced),
    }]})


def get_lyrics(st, who):
    artist = (request.values.get("artist") or "").strip().lower()
    title = (request.values.get("title") or "").strip().lower()
    if title:
        for track in st.db.subsonic_search_songs(title, 50, 0, who.scope):
            if (track.get("title") or "").lower() != title:
                continue
            if artist and artist not in (track.get("artist") or "").lower():
                continue
            found = _read_lyrics(track)
            if found:
                # Legacy getLyrics is plain text: drop the LRC timestamps.
                plain = "\n".join(ln["value"] for ln in _structured(*found))
                return ok(lyrics={"artist": track.get("artist") or "",
                                  "title": track.get("title") or "", "value": plain})
    return ok(lyrics={})


# ── Similar / top songs ───────────────────────────────────────────────────────

def _seed_tracks(st, who, sid: str) -> list:
    if sid.startswith("tr-"):
        return [_song_or_404(st, who, sid)]
    if sid.startswith("al-"):
        return _album(st, who, sid)[1]
    if sid.startswith("ar-"):
        return st.db.subsonic_artist_tracks(_artist_or_404(st, who, sid)["norm_name"], who.scope)
    raise SubsonicError(E_NOT_FOUND, "Unknown id.")


def _similar(st, who, seed: list, count: int) -> list:
    """Offline "similar": the seed's own artists first (shuffled), then tracks
    at a nearby tempo — BPM is what this library knows best. ±5 %, octave-folded
    like Run mode, so a 170 BPM seed also pulls 85 BPM tracks."""
    seen = {t["id"] for t in seed}
    picked: list = []
    norms = {normalize_artist_name(c) for t in seed for c in split_artist_credits(t.get("artist"))}
    same = []
    for n in norms:
        same += [t for t in st.db.subsonic_artist_tracks(n, who.scope) if t["id"] not in seen]
    random.shuffle(same)
    for t in same:
        if len(picked) >= count // 2:
            break
        if t["id"] not in seen:
            picked.append(t)
            seen.add(t["id"])
    bpms = [t["bpm"] for t in seed if t.get("bpm")]
    if bpms and len(picked) < count:
        picked += st.db.subsonic_bpm_neighbours(statistics.median(bpms), 0.05, sorted(seen),
                                                count - len(picked), who.scope)
    return picked[:count]


def get_similar_songs(st, who):
    rows = _similar(st, who, _seed_tracks(st, who, _req("id")), _int("count", 50, 1, 500))
    return ok(similarSongs={"song": _songs(st, rows)})


def get_similar_songs2(st, who):
    rows = _similar(st, who, _seed_tracks(st, who, _req("id")), _int("count", 50, 1, 500))
    return ok(similarSongs2={"song": _songs(st, rows)})


def get_top_songs(st, who):
    norm = normalize_artist_name(_req("artist"))
    rows = sorted(st.db.subsonic_artist_tracks(norm, who.scope),
                  key=lambda t: (-(t.get("play_count") or 0), (t.get("title") or "").lower()))
    return ok(topSongs={"song": _songs(st, rows[:_int("count", 50, 1, 500)])})


# ── Media ─────────────────────────────────────────────────────────────────────

def _source_kbps(path: str, track: dict):
    seconds = (track.get("duration_ms") or 0) / 1000
    try:
        return int(os.path.getsize(path) * 8 / seconds / 1000) if seconds else None
    except OSError:
        return None


def stream(st, who, as_attachment: bool = False):
    track = _song_or_404(st, who, _req("id"))
    real = _assert_in_music_dir(track["file_path"])
    if not os.path.isfile(real):
        raise SubsonicError(E_NOT_FOUND, "File is missing on disk.")
    if not as_attachment and st.config.get("subsonic_transcode"):
        target = transcode.plan(real, _source_kbps(real, track), request.values.get("format", ""),
                                _int("maxBitRate", 0, 0, 10_000))
        if target:
            try:
                offset = max(0.0, float(request.values.get("timeOffset") or 0))
            except ValueError:
                offset = 0.0
            resp = transcode.stream_response(
                real, *target, offset=offset,
                duration_s=int((track.get("duration_ms") or 0) / 1000) or None,
                estimate_length=(request.values.get("estimateContentLength") or "").lower() == "true")
            if resp is not None:
                return resp
    ext = os.path.splitext(real)[1].lower()
    return send_file(real, mimetype=views.CONTENT_TYPES.get(ext, "application/octet-stream"),
                     conditional=True, as_attachment=as_attachment,
                     download_name=os.path.basename(real))


def download(st, who):
    return stream(st, who, as_attachment=True)


def _folder_cover(directory: str):
    for name in _FOLDER_COVERS:
        p = os.path.join(directory, name)
        if os.path.isfile(p):
            with open(p, "rb") as f:
                return f.read(), "image/png" if name.endswith(".png") else "image/jpeg"
    return None


def _cover_for_track(track: dict):
    from ...grabber.tagging import read_cover
    path = _assert_in_music_dir(track["file_path"])
    return read_cover(path) or _folder_cover(os.path.dirname(path))


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


def _cover_candidates(st, who, cid: str) -> list:
    if cid.startswith("tr-"):
        return [_song_or_404(st, who, cid)]
    if cid.startswith("al-"):
        return st.db.subsonic_album_tracks(albums_index.keys_for(st.db, cid), who.scope)[:8]
    if cid.startswith("ar-"):
        row = artists_index.by_id(st.db, who.scope, cid)
        return st.db.subsonic_artist_tracks(row["norm_name"], who.scope)[:8] if row else []
    if cid.startswith(_RUN_PREFIX):
        preset = _run_preset_for(st, cid)
        return _run_playlist_tracks(st, who, preset[1])[:8] if preset else []
    if cid.startswith("pl-"):
        row = _playlist_or_404(st, who, cid)
        return st.db.subsonic_playlist_entries(row["id"])[:8]
    if cid.startswith("dir-"):
        folder = dir_index.get(st.db, st.music_dir, who.scope, cid)
        if folder is None:
            return []
        prefix = os.path.join(st.music_dir, *folder.rel.split("/")) if folder.rel else st.music_dir
        return st.db.subsonic_tracks_under(os.path.join(prefix, ""), who.scope)[:8]
    return []


def get_cover_art(st, who):
    for track in _cover_candidates(st, who, _req("id")):
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


def _set_star(st, who, starred: bool):
    """Song stars are the library's own star (the flag the web UI, Run mode and
    Navidrome star sync use). Album and artist stars live in subsonic_stars,
    keyed by id. All are library-wide, like song stars; a player can only star
    what its scope shows it."""
    for sid in _song_ids():
        st.db.set_starred(_song_or_404(st, who, sid)["file_path"], starred)
    for aid in [i for i in request.values.getlist("albumId") if i]:
        _album(st, who, aid)  # 70 unless visible to this account
        st.db.set_subsonic_star("album", aid, starred)
    for aid in [i for i in request.values.getlist("artistId") if i]:
        _artist_or_404(st, who, aid)
        st.db.set_subsonic_star("artist", aid, starred)
    return ok()


def star(st, who):
    return _set_star(st, who, True)


def unstar(st, who):
    return _set_star(st, who, False)


def _starred_body(st, who) -> dict:
    album_stars, artist_stars = _stars(st, "album"), _stars(st, "artist")
    albums = []
    for aid in album_stars:
        keys = albums_index.keys_for(st.db, aid)
        rows = st.db.subsonic_albums("alphabeticalByName", limit=len(keys), keys=keys,
                                     scope=who.scope) if keys else []
        if rows:
            albums.append(views.album(rows[0], album_stars))
    artists = [views.artist(row, artist_stars)
               for aid in artist_stars
               if (row := artists_index.by_id(st.db, who.scope, aid)) is not None]
    return {"artist": artists, "album": albums,
            "song": _songs(st, st.db.subsonic_starred_songs(who.scope))}


def get_starred2(st, who):
    return ok(starred2=_starred_body(st, who))


def get_starred(st, who):
    return ok(starred=_starred_body(st, who))


def scrobble(st, who):
    if request.values.get("submission", "true").lower() == "false":
        return ok()  # "now playing" — nothing to record
    times = request.values.getlist("time")
    for i, sid in enumerate(_song_ids()):
        track = _song_or_404(st, who, sid)
        path = track["file_path"]
        st.db.bump_play_count(path)
        played_at = None
        try:
            if i < len(times):
                played_at = datetime.fromtimestamp(int(times[i]) / 1000, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            played_at = None
        try:
            st.db.record_play_event(who.owner, path, duration_ms=track.get("duration_ms"),
                                    now=played_at)
        except Exception as exc:  # pragma: no cover - attribution is best effort
            log.debug("Subsonic play-event attribution failed: %s", exc)
        from ..api.media import forward_scrobble
        try:
            forward_scrobble(st, track)
        except Exception as exc:  # pragma: no cover - forwarding is best effort
            log.debug("Subsonic scrobble forward failed: %s", exc)
    return ok()


METHODS = {
    "ping": ping, "getLicense": get_license,
    "getOpenSubsonicExtensions": get_open_subsonic_extensions,
    "getMusicFolders": get_music_folders, "getUser": get_user,
    "getScanStatus": get_scan_status, "startScan": start_scan,
    # ID3 browsing
    "getArtists": get_artists, "getArtist": get_artist, "getAlbum": get_album,
    "getSong": get_song, "getAlbumList2": get_album_list2, "getAlbumList": get_album_list,
    "getGenres": get_genres, "getSongsByGenre": get_songs_by_genre,
    "getRandomSongs": get_random_songs, "search3": search3, "search2": search2,
    "getNowPlaying": get_now_playing,
    "getArtistInfo2": get_artist_info2, "getArtistInfo": get_artist_info,
    "getAlbumInfo2": get_album_info2, "getAlbumInfo": get_album_info2,
    # Folder browsing
    "getIndexes": get_indexes, "getMusicDirectory": get_music_directory,
    # Playlists
    "getPlaylists": get_playlists, "getPlaylist": get_playlist,
    "createPlaylist": create_playlist, "updatePlaylist": update_playlist,
    "deletePlaylist": delete_playlist,
    # Lyrics
    "getLyricsBySongId": get_lyrics_by_song_id, "getLyrics": get_lyrics,
    # Similar / top
    "getSimilarSongs": get_similar_songs, "getSimilarSongs2": get_similar_songs2,
    "getTopSongs": get_top_songs,
    # Media
    "stream": stream, "download": download, "getCoverArt": get_cover_art,
    # Annotation
    "star": star, "unstar": unstar, "getStarred2": get_starred2, "getStarred": get_starred,
    "scrobble": scrobble,
}
