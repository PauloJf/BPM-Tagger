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
"""

from datetime import datetime, timezone
from typing import Iterable, Optional

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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
                              scope: Optional[list] = None) -> list[dict]:
        where, params = scope_sql(scope)
        where += " AND status = 'done'"
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
        if order == "starred":
            having = " HAVING MAX(COALESCE(starred, 0)) = 1"
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
