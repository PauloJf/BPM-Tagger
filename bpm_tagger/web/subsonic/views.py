"""DB rows → Subsonic ``Child`` / ``AlbumID3`` / ``ArtistID3`` dicts."""

import os
from datetime import datetime, timezone
from typing import Optional

from ...text import split_artist_credits, split_genres
from . import ids

CONTENT_TYPES = {
    ".mp3": "audio/mpeg", ".flac": "audio/flac", ".ogg": "audio/ogg",
    ".opus": "audio/ogg", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".wav": "audio/wav", ".wv": "audio/x-wavpack",
}

# Prefixes the Subsonic artist index sorts past (getArtists ignoredArticles).
IGNORED_ARTICLES = "The El La Los Las Le Les"
_ARTICLES = tuple(a.lower() + " " for a in IGNORED_ARTICLES.split())


def iso(ts: Optional[str]) -> Optional[str]:
    """Stored timestamps (ISO, maybe without tz) → ``2026-09-23T12:00:00.000Z``."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
        f"{dt.microsecond // 1000:03d}Z"


def _first_artist(track: dict) -> str:
    credits = split_artist_credits(track.get("artist"))
    return credits[0] if credits else (track.get("artist") or "")


def song(track: dict, music_dir: str) -> dict:
    path = track["file_path"]
    ext = os.path.splitext(path)[1].lower()
    try:
        size = os.path.getsize(path)
    except OSError:
        size = None
    seconds = int((track.get("duration_ms") or 0) / 1000) or None
    try:
        rel = os.path.relpath(path, music_dir).replace(os.sep, "/")
    except ValueError:  # different drive on Windows
        rel = os.path.basename(path)
    if rel.startswith(".."):
        rel = os.path.basename(path)
    # Folder-browsing clients navigate up through `parent`, so it names the
    # containing directory; ID3 clients use albumId.
    parent = ids.dir_id(rel.rsplit("/", 1)[0] if "/" in rel else "")
    artist = track.get("artist") or ""
    aid = ids.track_album_id(track)
    # No star timestamp is stored; the analysis time stands in for it.
    starred = ((iso(track.get("analyzed_at")) or "1970-01-01T00:00:00.000Z")
               if track.get("starred") else None)
    return {
        "id": ids.song_id(track),
        "parent": parent,
        "isDir": False,
        "title": track.get("title") or os.path.splitext(os.path.basename(path))[0],
        "album": track.get("album") or None,
        "artist": artist or None,
        "track": track.get("track_no"),
        "discNumber": track.get("disc_no"),
        "year": track.get("year"),
        "coverArt": ids.song_id(track),
        "size": size,
        "contentType": CONTENT_TYPES.get(ext, "application/octet-stream"),
        "suffix": ext.lstrip(".") or None,
        "duration": seconds,
        "bitRate": int(size * 8 / seconds / 1000) if size and seconds else None,
        "path": rel,
        "playCount": track.get("play_count") or 0,
        "played": iso(track.get("last_played")),
        "starred": starred,
        "created": iso(track.get("analyzed_at")),
        "albumId": aid,
        "artistId": ids.artist_id(_first_artist(track)) if artist else None,
        "type": "music",
        "mediaType": "song",
        "isVideo": False,
        "genre": (split_genres(track.get("genre")) or [None])[0],
        # OpenSubsonic: every genre, not just the first.
        "genres": [{"name": g} for g in split_genres(track.get("genre"))] or None,
        # OpenSubsonic: the whole point of this server.
        "bpm": int(round(track["bpm"])) if track.get("bpm") else None,
        "isrc": [track["isrc"]] if track.get("isrc") else None,
    }


def album(row: dict, stars: dict | None = None) -> dict:
    """``row`` is an aggregate from ``db.subsonic_albums``; ``stars`` is the
    ``{album_id: starred_at}`` map (explicit album stars)."""
    aid = ids.album_id(row["name"], row.get("album_artist") or "")
    artist = row.get("artist") or ""
    return {
        "id": aid,
        "name": row["name"],
        "album": row["name"],
        "title": row["name"],
        "artist": artist or None,
        "artistId": ids.artist_id(split_artist_credits(artist)[0]) if artist else None,
        "coverArt": aid,
        "songCount": row.get("song_count") or 0,
        "duration": int((row.get("duration_ms") or 0) / 1000),
        "playCount": row.get("play_count") or 0,
        "played": iso(row.get("last_played")),
        "created": iso(row.get("created")) or "1970-01-01T00:00:00.000Z",
        "year": row.get("year"),
        "genre": (split_genres(row.get("genre")) or [None])[0],
        "starred": iso((stars or {}).get(aid)),
        "isDir": True,
        "mediaType": "album",
    }


def artist(row: dict, stars: dict | None = None) -> dict:
    """``row`` is an entry from ``db.subsonic_artists`` (or ``list_artists``)."""
    aid = ids.artist_id_norm(row["norm_name"]) if row.get("norm_name") else ids.artist_id(row["name"])
    return {"id": aid, "name": row["name"], "coverArt": aid, "albumCount": row.get("albums") or 0,
            "starred": iso((stars or {}).get(aid))}


def playlist(row: dict, owner_name: str, writable: bool) -> dict:
    """``row`` is from ``db.subsonic_playlist_summaries``. Only Local playlists
    are writable over Subsonic — Spotify/Navidrome mirrors belong to their source."""
    pid = ids.playlist_id(row["id"])
    created = iso(row.get("created_at")) or "1970-01-01T00:00:00.000Z"
    return {
        "id": pid,
        "name": row.get("name") or "",
        "comment": row.get("description") or None,
        "owner": owner_name,
        "public": False,
        "songCount": row.get("song_count") or 0,
        "duration": int((row.get("duration_ms") or 0) / 1000),
        "created": created,
        "changed": iso(row.get("changed_at")) or iso(row.get("last_synced_at")) or created,
        "coverArt": pid,
        "readonly": not writable,
    }


def directory_child(rel: str, parent_rel: str) -> dict:
    did = ids.dir_id(rel)
    name = rel.rsplit("/", 1)[-1]
    return {"id": did, "parent": ids.dir_id(parent_rel), "isDir": True,
            "title": name, "name": name, "coverArt": did}


def index_letter(name: str) -> str:
    n = (name or "").strip()
    low = n.lower()
    for art in _ARTICLES:
        if low.startswith(art):
            n = n[len(art):]
            break
    ch = n[:1].upper()
    return ch if ch.isalpha() else "#"


def sort_name(name: str) -> str:
    low = (name or "").strip().lower()
    for art in _ARTICLES:
        if low.startswith(art):
            return low[len(art):]
    return low
