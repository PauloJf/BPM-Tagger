"""Queries behind the optional Subsonic API (docs/plans/subsonic-api.md).

Pure SQL over the existing tables — no schema of its own beyond the
``subsonic_credentials`` table in ``base.py``. Albums have no table: an album is
the ``(album, COALESCE(album_artist, ''))`` group, the same grouping
``list_albums`` uses, so the Subsonic view and the web UI never disagree about
what an album is.

**Scope.** Every library query takes ``scope``: ``None`` for the whole library
(admin), or a list of playlist ids for a player user, who may only see the
tracks those playlists hold (the same rule as Run mode). The restriction is a
subquery on the playlist ids, so it costs one parameter per playlist rather than
one per track.

**Album index.** Unscoped album lists read ``subsonic_album_index``, a
precomputed aggregate of ``tracks``. SQLite triggers on ``tracks`` flag it dirty
on any change that affects an album, and the next read rebuilds it in one pass.
The triggers exist only while the Subsonic API is enabled
(``ensure_album_index`` / ``drop_album_index_triggers``), so a core-only
install's scans never touch it. Scoped (player) lists still aggregate on the
fly, over just the tracks the player may see.
"""

import hashlib
import threading
import time
from datetime import datetime, timezone
from typing import Iterable, Optional

from ..text import normalize_artist_name, normalize_genre

_LIVE = "status != 'deleted'"

# getAlbumList2 `type` → ORDER BY over the album aggregate below. Whitelisted:
# the value is interpolated into SQL, never a client string.
ALBUM_ORDERS = {
    "random":               "RANDOM()",
    "newest":               "created DESC, name COLLATE NOCASE",
    "recent":               "last_played IS NULL, last_played DESC, name COLLATE NOCASE",
    "frequent":             "play_count DESC, name COLLATE NOCASE",
    "starred":              "name COLLATE NOCASE",
    "alphabeticalByName":   "name COLLATE NOCASE, artist COLLATE NOCASE",
    "alphabeticalByArtist": "artist COLLATE NOCASE, year, name COLLATE NOCASE",
    "byYear":               "year, name COLLATE NOCASE",
}

_ALBUM_AGG = """
    SELECT album AS name,
           MIN(NULLIF(genre, '')) AS genre,
           COALESCE(album_artist, '') AS album_artist,
           COALESCE(NULLIF(album_artist, ''), MIN(artist), '') AS artist,
           COUNT(*) AS song_count,
           COALESCE(SUM(duration_ms), 0) AS duration_ms,
           MAX(year) AS year,
           MIN(analyzed_at) AS created,
           COALESCE(SUM(play_count), 0) AS play_count,
           MAX(last_played) AS last_played,
           MAX(COALESCE(starred, 0)) AS starred,
           MIN(id) AS sample_id
    FROM tracks
"""
_ALBUM_GROUP = " GROUP BY album, COALESCE(album_artist, '')"
_KEY_MATCH = "(album = ? AND COALESCE(album_artist, '') = ?)"


# Columns whose change can alter an album aggregate (or album membership).
_ALBUM_TRIGGER_COLS = ("album", "album_artist", "artist", "duration_ms", "year", "status",
                       "play_count", "last_played", "analyzed_at", "genre")
_TRIGGERS = ("sub_albums_ins", "sub_albums_del", "sub_albums_upd")
_rebuild_lock = threading.Lock()
# During a scan the index is dirty after nearly every write, and a rebuild costs
# ~0.3 s per 50k tracks — so rebuild at most this often (the first build of a
# process is always immediate). Browse lists may lag a running scan by this much.
ALBUM_INDEX_MIN_INTERVAL = 15.0
_last_rebuild: dict = {}   # db_path → monotonic time of the last rebuild


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def subsonic_album_id(album: str, album_artist: str) -> str:
    """The stable Subsonic album id: ``al-`` + sha1 of the normalized
    (album_artist, album) pair. Lives here so the index can be rebuilt without
    the web layer; ``web/subsonic/ids.album_id`` is this function."""
    key = normalize_artist_name(album_artist or "") + "\x1f" + normalize_artist_name(album or "")
    return "al-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def scope_sql(scope: Optional[list], col: str = "id") -> tuple[str, list]:
    """``AND <col> IN (tracks of these playlists)`` — or nothing for full scope."""
    if scope is None:
        return "", []
    if not scope:
        return " AND 0", []
    marks = ",".join("?" * len(scope))
    return (f" AND {col} IN (SELECT t2.id FROM playlist_tracks pt "
            f"JOIN tracks t2 ON t2.file_path = pt.matched_file_path "
            f"WHERE pt.playlist_id IN ({marks}) AND pt.removed_at IS NULL "
            f"AND pt.match_status = 'have')"), list(scope)


def _keys_sql(keys: list) -> tuple[str, list]:
    return " AND (" + " OR ".join([_KEY_MATCH] * len(keys)) + ")", [v for k in keys for v in k]


class SubsonicMixin:
    # ── Credentials ──────────────────────────────────────────────────────────
    def get_subsonic_credentials(self, owner: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM subsonic_credentials WHERE owner = ?",
                               (owner,)).fetchone()
        return dict(row) if row else None

    def list_subsonic_credentials(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM subsonic_credentials ORDER BY owner").fetchall()
        return [dict(r) for r in rows]

    def _upsert_subsonic(self, owner: str, column: str, value: Optional[str]) -> None:
        assert column in ("api_key_hash", "password")
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO subsonic_credentials (owner, {column}, created_at) VALUES (?, ?, ?) "
                f"ON CONFLICT(owner) DO UPDATE SET {column} = excluded.{column}",
                (owner, value, _now()))

    def set_subsonic_api_key_hash(self, owner: str, key_hash: Optional[str]) -> None:
        self._upsert_subsonic(owner, "api_key_hash", key_hash)

    def set_subsonic_password(self, owner: str, password: Optional[str]) -> None:
        self._upsert_subsonic(owner, "password", password)

    def delete_subsonic_credentials(self, owner: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM subsonic_credentials WHERE owner = ?", (owner,))

    def find_subsonic_owner_by_key_hash(self, key_hash: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute("SELECT owner FROM subsonic_credentials WHERE api_key_hash = ?",
                               (key_hash,)).fetchone()
        return row["owner"] if row else None

    def touch_subsonic_credentials(self, owner: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE subsonic_credentials SET last_used_at = ? WHERE owner = ?",
                         (_now(), owner))

    # ── Album / artist stars ─────────────────────────────────────────────────
    def set_subsonic_star(self, kind: str, item_id: str, starred: bool) -> None:
        assert kind in ("album", "artist")
        with self._connect() as conn:
            if starred:
                conn.execute("INSERT OR IGNORE INTO subsonic_stars (kind, item_id, starred_at) "
                             "VALUES (?, ?, ?)", (kind, item_id, _now()))
            else:
                conn.execute("DELETE FROM subsonic_stars WHERE kind = ? AND item_id = ?",
                             (kind, item_id))

    def subsonic_star_map(self, kind: str) -> dict:
        """{item_id: starred_at} for one kind."""
        with self._connect() as conn:
            rows = conn.execute("SELECT item_id, starred_at FROM subsonic_stars WHERE kind = ? "
                                "ORDER BY starred_at DESC", (kind,)).fetchall()
        return {r["item_id"]: r["starred_at"] for r in rows}

    # ── Genres ───────────────────────────────────────────────────────────────
    def subsonic_genres(self, scope: Optional[list] = None) -> list[dict]:
        """Genre name (the most common spelling), song count and album count."""
        where, params = scope_sql(scope, "t.id")
        with self._connect() as conn:
            rows = conn.execute(f"""
                WITH g AS (
                    SELECT tg.norm_name, tg.name, t.album, COALESCE(t.album_artist, '') AS aa
                    FROM track_genres tg JOIN tracks t ON t.id = tg.track_id
                    WHERE t.status != 'deleted'{where}
                ),
                display AS (
                    SELECT norm_name, name FROM (
                        SELECT norm_name, name, ROW_NUMBER() OVER (
                            PARTITION BY norm_name ORDER BY COUNT(*) DESC, name) AS rn
                        FROM g GROUP BY norm_name, name) WHERE rn = 1
                )
                SELECT d.name AS name, COUNT(*) AS song_count,
                       COUNT(DISTINCT CASE WHEN COALESCE(g.album, '') != ''
                                      THEN g.album || char(31) || g.aa END) AS album_count
                FROM g JOIN display d ON d.norm_name = g.norm_name
                GROUP BY g.norm_name ORDER BY d.name COLLATE NOCASE
            """, params).fetchall()
        return [dict(r) for r in rows]

    def subsonic_genre_songs(self, genre: str, count: int, offset: int,
                             scope: Optional[list] = None) -> list[dict]:
        where, params = scope_sql(scope, "t.id")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT t.* FROM tracks t JOIN track_genres tg ON tg.track_id = t.id "
                f"WHERE t.status != 'deleted' AND tg.norm_name = ?{where} "
                "ORDER BY t.artist COLLATE NOCASE, t.album COLLATE NOCASE, t.disc_no, t.track_no "
                "LIMIT ? OFFSET ?", [normalize_genre(genre), *params, count, offset]).fetchall()
        return [dict(r) for r in rows]

    def subsonic_genre_album_keys(self, genre: str, scope: Optional[list] = None) -> list:
        where, params = scope_sql(scope, "t.id")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT t.album, COALESCE(t.album_artist, '') AS aa FROM tracks t "
                "JOIN track_genres tg ON tg.track_id = t.id "
                f"WHERE t.status != 'deleted' AND COALESCE(t.album, '') != '' "
                f"AND tg.norm_name = ?{where}", [normalize_genre(genre), *params]).fetchall()
        return [(r["album"], r["aa"]) for r in rows]

    # ── Album index ──────────────────────────────────────────────────────────
    def ensure_album_index(self) -> None:
        """Create the dirty-flag triggers (idempotent) and mark the index stale.
        Called when the Subsonic API is registered."""
        cols = ", ".join(_ALBUM_TRIGGER_COLS)
        mark = ("INSERT INTO subsonic_meta (key, value) VALUES ('albums_dirty', '1') "
                "ON CONFLICT(key) DO UPDATE SET value = '1';")
        with self._connect() as conn:
            conn.execute(f"CREATE TRIGGER IF NOT EXISTS sub_albums_ins AFTER INSERT ON tracks "
                         f"BEGIN {mark} END")
            conn.execute(f"CREATE TRIGGER IF NOT EXISTS sub_albums_del AFTER DELETE ON tracks "
                         f"BEGIN {mark} END")
            conn.execute(f"CREATE TRIGGER IF NOT EXISTS sub_albums_upd AFTER UPDATE OF {cols} "
                         f"ON tracks BEGIN {mark} END")
            conn.execute(mark.rstrip(";"))

    def drop_album_index_triggers(self) -> None:
        """Remove the triggers (API disabled): scans stop paying the few-byte
        write per tracks change. The index table stays, and is rebuilt on the
        next ensure_album_index()."""
        with self._connect() as conn:
            for name in _TRIGGERS:
                conn.execute(f"DROP TRIGGER IF EXISTS {name}")

    def album_index_dirty(self) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM subsonic_meta WHERE key = 'albums_dirty'"
                               ).fetchone()
        return row is None or row["value"] != "0"

    def refresh_album_index(self, force: bool = False) -> bool:
        """Rebuild the index if dirty (or ``force``), at most once per
        ``ALBUM_INDEX_MIN_INTERVAL``. Returns True if it rebuilt.

        The flag is cleared *before* reading tracks, inside the same write
        transaction, so a change landing mid-rebuild re-flags it and the next
        read picks it up. The album id is computed in Python (normalization),
        which is why this isn't a single INSERT ... SELECT."""
        with _rebuild_lock:
            if not force:
                last = _last_rebuild.get(self.db_path)
                if last is not None and time.monotonic() - last < ALBUM_INDEX_MIN_INTERVAL:
                    return False
                if not self.album_index_dirty():
                    return False
            with self._connect() as conn:
                conn.execute("INSERT INTO subsonic_meta (key, value) VALUES ('albums_dirty', '0') "
                             "ON CONFLICT(key) DO UPDATE SET value = '0'")
                rows = conn.execute(
                    _ALBUM_AGG + f" WHERE {_LIVE} AND COALESCE(album, '') != ''" + _ALBUM_GROUP
                ).fetchall()
                conn.execute("DELETE FROM subsonic_album_index")
                conn.executemany(
                    "INSERT INTO subsonic_album_index (album_id, name, album_artist, artist, "
                    "song_count, duration_ms, year, created, play_count, last_played, genre, "
                    "sample_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(subsonic_album_id(r["name"], r["album_artist"]), r["name"], r["album_artist"],
                      r["artist"], r["song_count"], r["duration_ms"], r["year"], r["created"],
                      r["play_count"], r["last_played"], r["genre"], r["sample_id"])
                     for r in rows])
            _last_rebuild[self.db_path] = time.monotonic()
            return True

    # ── Songs ────────────────────────────────────────────────────────────────
    def _tracks(self, where: str, params: list, tail: str = "") -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM tracks WHERE {_LIVE}{where}{tail}",
                                params).fetchall()
        return [dict(r) for r in rows]

    def get_track_by_id(self, track_id: int, scope: Optional[list] = None) -> Optional[dict]:
        s, sp = scope_sql(scope)
        rows = self._tracks(" AND id = ?" + s, [track_id, *sp])
        return rows[0] if rows else None

    def subsonic_search_songs(self, query: str, count: int, offset: int,
                              scope: Optional[list] = None) -> list[dict]:
        """Songs whose title, artist or album contains ``query``. An empty query
        pages through the whole library — clients such as Symfonium sync their
        local index that way."""
        where, params = scope_sql(scope)
        if query:
            like = f"%{query}%"
            where += " AND (title LIKE ? OR artist LIKE ? OR album LIKE ?)"
            params += [like, like, like]
        return self._tracks(where, params + [count, offset], " ORDER BY id LIMIT ? OFFSET ?")

    def subsonic_random_songs(self, size: int, from_year: Optional[int] = None,
                              to_year: Optional[int] = None,
                              scope: Optional[list] = None,
                              genre: Optional[str] = None) -> list[dict]:
        where, params = scope_sql(scope)
        where += " AND status = 'done'"
        if genre:
            where += " AND id IN (SELECT track_id FROM track_genres WHERE norm_name = ?)"
            params.append(normalize_genre(genre))
        if from_year is not None:
            where += " AND year >= ?"
            params.append(from_year)
        if to_year is not None:
            where += " AND year <= ?"
            params.append(to_year)
        return self._tracks(where, params + [size], " ORDER BY RANDOM() LIMIT ?")

    def subsonic_starred_songs(self, scope: Optional[list] = None) -> list[dict]:
        where, params = scope_sql(scope)
        return self._tracks(where + " AND starred = 1", params,
                            " ORDER BY artist COLLATE NOCASE, album COLLATE NOCASE, disc_no, track_no")

    def subsonic_tracks_by_paths(self, paths: list[str]) -> dict:
        """{file_path: track} for the given paths (live rows only)."""
        out = {}
        for i in range(0, len(paths), 500):
            chunk = paths[i:i + 500]
            marks = ",".join("?" * len(chunk))
            for t in self._tracks(f" AND file_path IN ({marks})", chunk):
                out[t["file_path"]] = t
        return out

    def subsonic_all_paths(self, scope: Optional[list] = None) -> list[str]:
        where, params = scope_sql(scope)
        with self._connect() as conn:
            rows = conn.execute(f"SELECT file_path FROM tracks WHERE {_LIVE}{where}",
                                params).fetchall()
        return [r["file_path"] for r in rows]

    def subsonic_tracks_under(self, prefix: str, scope: Optional[list] = None) -> list[dict]:
        """Live tracks whose path starts with ``prefix`` (a directory + separator)."""
        esc = prefix.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        where, params = scope_sql(scope)
        return self._tracks(" AND file_path LIKE ? ESCAPE '!'" + where, [esc + "%", *params],
                            " ORDER BY disc_no, track_no, file_path")

    def subsonic_count_tracks(self, scope: Optional[list] = None) -> int:
        where, params = scope_sql(scope)
        with self._connect() as conn:
            return conn.execute(f"SELECT COUNT(*) FROM tracks WHERE {_LIVE}{where}",
                                params).fetchone()[0]

    def count_live_tracks(self) -> int:
        return self.subsonic_count_tracks(None)

    # ── Artists ──────────────────────────────────────────────────────────────
    def subsonic_artists(self, scope: Optional[list] = None) -> list[dict]:
        """One row per individually credited artist (via track_artists), like
        ``list_artists`` — but scoped, and with the norm key the id derives from."""
        where, params = scope_sql(scope, "t.id")
        with self._connect() as conn:
            rows = conn.execute(f"""
                WITH live AS (
                    SELECT ta.norm_name, ta.name, t.album
                    FROM track_artists ta JOIN tracks t ON t.id = ta.track_id
                    WHERE t.status != 'deleted'{where}
                ),
                display AS (
                    SELECT norm_name, name FROM (
                        SELECT norm_name, name, ROW_NUMBER() OVER (
                                   PARTITION BY norm_name
                                   ORDER BY COUNT(*) DESC, name COLLATE NOCASE) AS rn
                        FROM live GROUP BY norm_name, name
                    ) WHERE rn = 1
                )
                SELECT d.name AS name, l.norm_name AS norm_name, COUNT(*) AS tracks,
                       COUNT(DISTINCT NULLIF(l.album, '')) AS albums
                FROM live l JOIN display d ON d.norm_name = l.norm_name
                GROUP BY l.norm_name ORDER BY name COLLATE NOCASE
            """, params).fetchall()
        return [dict(r) for r in rows]

    def subsonic_artist_tracks(self, norm_name: str, scope: Optional[list] = None) -> list[dict]:
        where, params = scope_sql(scope, "t.id")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT t.* FROM tracks t JOIN track_artists ta ON ta.track_id = t.id "
                f"WHERE t.status != 'deleted' AND ta.norm_name = ?{where} "
                "ORDER BY t.album, t.disc_no, t.track_no, t.file_path",
                [norm_name, *params]).fetchall()
        return [dict(r) for r in rows]

    # ── Albums ───────────────────────────────────────────────────────────────
    def subsonic_album_keys(self) -> list[tuple[str, str]]:
        """Every (album, album_artist) group — the input for the id map. Global:
        the map only names albums; whether a caller may see one is decided by
        the scoped queries that fetch it."""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT album, COALESCE(album_artist, '') AS aa FROM tracks "
                f"WHERE {_LIVE} AND COALESCE(album, '') != ''").fetchall()
        return [(r["album"], r["aa"]) for r in rows]

    def subsonic_album_id_map(self) -> dict:
        """{album_id: [(album, album_artist), ...]} straight from the index."""
        self.refresh_album_index()
        out: dict = {}
        with self._connect() as conn:
            for r in conn.execute("SELECT album_id, name, album_artist FROM subsonic_album_index"):
                out.setdefault(r["album_id"], []).append((r["name"], r["album_artist"]))
        return out

    def subsonic_albums(self, order: str = "alphabeticalByName", limit: int = 10,
                        offset: int = 0, from_year: Optional[int] = None,
                        to_year: Optional[int] = None,
                        keys: Optional[Iterable[tuple[str, str]]] = None,
                        scope: Optional[list] = None) -> list[dict]:
        """Album aggregates, ordered per ``ALBUM_ORDERS`` (unknown → by name).

        ``keys`` restricts to specific (album, album_artist) groups (an artist's
        albums, search hits). ``order='starred'`` keeps albums with any starred
        track; ``byYear`` with from > to sorts newest first, as the spec asks.
        A scoped caller's aggregates count only the tracks they may see."""
        if scope is None:
            return self._indexed_albums(order, limit, offset, from_year, to_year, keys)
        order_sql = ALBUM_ORDERS.get(order, ALBUM_ORDERS["alphabeticalByName"])
        where, params = scope_sql(scope)
        where = f" WHERE {_LIVE} AND COALESCE(album, '') != ''" + where
        if keys is not None:
            keys = list(keys)
            if not keys:
                return []
            ks, kp = _keys_sql(keys)
            where += ks
            params += kp
        having = ""
        if order == "byYear" and (from_year is not None or to_year is not None):
            lo, hi = from_year, to_year
            if lo is not None and hi is not None and lo > hi:
                lo, hi = hi, lo
                order_sql = "year DESC, name COLLATE NOCASE"
            conds = []
            if lo is not None:
                conds.append("MAX(year) >= ?")
                params.append(lo)
            if hi is not None:
                conds.append("MAX(year) <= ?")
                params.append(hi)
            having = " HAVING " + " AND ".join(conds)
        sql = (_ALBUM_AGG + where + _ALBUM_GROUP + having +
               f" ORDER BY {order_sql} LIMIT ? OFFSET ?")
        with self._connect() as conn:
            rows = conn.execute(sql, params + [limit, offset]).fetchall()
        return [dict(r) for r in rows]

    def _indexed_albums(self, order, limit, offset, from_year, to_year, keys) -> list[dict]:
        """``subsonic_albums`` for the whole library, off the precomputed index.
        Same ordering and year rules as the aggregate path."""
        self.refresh_album_index()
        order_sql = ALBUM_ORDERS.get(order, ALBUM_ORDERS["alphabeticalByName"])
        where, params = " WHERE 1", []
        if keys is not None:
            keys = list(keys)
            if not keys:
                return []
            where += " AND (" + " OR ".join(["(name = ? AND album_artist = ?)"] * len(keys)) + ")"
            params += [v for k in keys for v in k]
        if order == "byYear" and (from_year is not None or to_year is not None):
            lo, hi = from_year, to_year
            if lo is not None and hi is not None and lo > hi:
                lo, hi = hi, lo
                order_sql = "year DESC, name COLLATE NOCASE"
            if lo is not None:
                where += " AND year >= ?"
                params.append(lo)
            if hi is not None:
                where += " AND year <= ?"
                params.append(hi)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM subsonic_album_index{where} ORDER BY {order_sql} LIMIT ? OFFSET ?",
                params + [limit, offset]).fetchall()
        return [dict(r) for r in rows]

    def subsonic_album_tracks(self, keys: Iterable[tuple[str, str]],
                              scope: Optional[list] = None) -> list[dict]:
        keys = list(keys)
        if not keys:
            return []
        ks, kp = _keys_sql(keys)
        s, sp = scope_sql(scope)
        return self._tracks(ks + s, kp + sp, " ORDER BY disc_no, track_no, file_path")

    def subsonic_search_album_keys(self, query: str, count: int, offset: int,
                                   scope: Optional[list] = None) -> list[tuple[str, str]]:
        where, params = scope_sql(scope)
        if query:
            where += " AND (album LIKE ? OR album_artist LIKE ?)"
            params += [f"%{query}%", f"%{query}%"]
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT album, COALESCE(album_artist, '') AS aa FROM tracks "
                f"WHERE {_LIVE} AND COALESCE(album, '') != ''{where} "
                "GROUP BY album, COALESCE(album_artist, '') "
                "ORDER BY album COLLATE NOCASE LIMIT ? OFFSET ?",
                params + [count, offset]).fetchall()
        return [(r["album"], r["aa"]) for r in rows]

    # ── Similar / top ────────────────────────────────────────────────────────
    def subsonic_bpm_neighbours(self, bpm: float, tolerance: float, exclude_ids: list,
                                limit: int, scope: Optional[list] = None) -> list[dict]:
        """Analyzed tracks within ±tolerance (fraction) of ``bpm`` — octave-folded,
        so an 85 BPM track neighbours a 170 BPM seed, as in Run mode."""
        where, params = scope_sql(scope)
        bands = []
        for b in (bpm, bpm / 2, bpm * 2):
            bands.append("(bpm BETWEEN ? AND ?)")
            params += [b * (1 - tolerance), b * (1 + tolerance)]
        where += " AND status = 'done' AND (" + " OR ".join(bands) + ")"
        if exclude_ids:
            where += f" AND id NOT IN ({','.join('?' * len(exclude_ids))})"
            params += list(exclude_ids)
        return self._tracks(where, params + [limit], " ORDER BY RANDOM() LIMIT ?")

    # ── Playlists ────────────────────────────────────────────────────────────
    def subsonic_playlist_entries(self, playlist_id: int) -> list[dict]:
        """The playable entries of a playlist, in order: live rows matched to a
        live library file. Each carries the library track plus ``pt_id`` (the
        playlist row), which updatePlaylist's songIndexToRemove resolves through."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT pt.id AS pt_id, t.* FROM playlist_tracks pt "
                "JOIN tracks t ON t.file_path = pt.matched_file_path AND t.status != 'deleted' "
                "WHERE pt.playlist_id = ? AND pt.removed_at IS NULL AND pt.match_status = 'have' "
                "ORDER BY pt.position, pt.id", (playlist_id,)).fetchall()
        return [dict(r) for r in rows]

    def subsonic_playlist_summaries(self, ids: Optional[list] = None) -> list[dict]:
        """Playlists with their playable entry count and duration, pinned first."""
        where, params = "", []
        if ids is not None:
            if not ids:
                return []
            where = f" WHERE p.id IN ({','.join('?' * len(ids))})"
            params = list(ids)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT p.*, "
                "(SELECT COUNT(*) FROM playlist_tracks pt JOIN tracks t "
                "   ON t.file_path = pt.matched_file_path AND t.status != 'deleted' "
                "   WHERE pt.playlist_id = p.id AND pt.removed_at IS NULL "
                "   AND pt.match_status = 'have') AS song_count, "
                "(SELECT COALESCE(SUM(t.duration_ms), 0) FROM playlist_tracks pt JOIN tracks t "
                "   ON t.file_path = pt.matched_file_path AND t.status != 'deleted' "
                "   WHERE pt.playlist_id = p.id AND pt.removed_at IS NULL "
                "   AND pt.match_status = 'have') AS duration_ms, "
                "(SELECT MAX(COALESCE(pt.added_at, pt.first_seen_at)) FROM playlist_tracks pt "
                "   WHERE pt.playlist_id = p.id) AS changed_at "
                f"FROM playlists p{where} ORDER BY p.pinned DESC, p.name COLLATE NOCASE",
                params).fetchall()
        return [dict(r) for r in rows]
