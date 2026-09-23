"""Stable, opaque Subsonic ids.

* song   ``tr-<tracks.id>`` — the autoincrement key survives rescans (rows are
  keyed by file_path, never re-created for an unchanged file);
* album  ``al-<16 hex>`` — sha1 of the normalized (album_artist, album) pair;
* artist ``ar-<16 hex>`` — sha1 of the normalized artist name (``track_artists.norm_name``);
* playlist ``pl-<playlists.id>``;
* directory ``dir-<16 hex>`` — sha1 of the path relative to MUSIC_DIR (``/``-separated,
  ``""`` for the root), for folder-browsing clients.

Hash ids stay stable across rescans and even DB rebuilds, as long as the tags
don't change. Resolving an album id back to its groups needs the id map, which
is rebuilt from the DB at most every few seconds.
"""

import hashlib
import threading
import time
from typing import Optional

from ...text import normalize_artist_name

_MAP_TTL = 10.0  # seconds; an album tagged a moment ago shows up within this


def _h(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def song_id(track: dict) -> str:
    return f"tr-{track['id']}"


def parse_song_id(sid: str) -> Optional[int]:
    if sid.startswith("tr-") and sid[3:].isdigit():
        return int(sid[3:])
    return None


def album_id(album: str, album_artist: str) -> str:
    return "al-" + _h(normalize_artist_name(album_artist or "") + "\x1f" +
                      normalize_artist_name(album or ""))


def artist_id_norm(norm_name: str) -> str:
    return "ar-" + _h(norm_name or "")


def artist_id(name: str) -> str:
    return artist_id_norm(normalize_artist_name(name or ""))


def playlist_id(pid: int) -> str:
    return f"pl-{pid}"


def parse_playlist_id(pid: str) -> Optional[int]:
    if pid.startswith("pl-") and pid[3:].isdigit():
        return int(pid[3:])
    return int(pid) if pid.isdigit() else None  # some clients strip the prefix


def dir_id(rel: str) -> str:
    return "dir-" + _h("dir\x1f" + rel)


def track_album_id(track: dict) -> Optional[str]:
    if not track.get("album"):
        return None
    return album_id(track["album"], track.get("album_artist") or "")


class AlbumIndex:
    """album id → [(album, album_artist), ...] (normalization can merge groups
    that differ only in case or accents — they are one album to a client)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._built = 0.0
        self._map: dict[str, list[tuple[str, str]]] = {}

    def keys_for(self, db, aid: str) -> list[tuple[str, str]]:
        with self._lock:
            age = time.monotonic() - self._built
            # A miss may be a brand-new album — rebuild, but at most once a second
            # so a stream of bogus ids can't turn every request into a table scan.
            if age > _MAP_TTL or (aid not in self._map and age > 1.0):
                m: dict[str, list[tuple[str, str]]] = {}
                for album, aa in db.subsonic_album_keys():
                    m.setdefault(album_id(album, aa), []).append((album, aa))
                self._map, self._built = m, time.monotonic()
            return list(self._map.get(aid, []))
