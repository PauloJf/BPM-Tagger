"""Queries behind the optional Subsonic API (docs/plans/subsonic-api.md).

Pure SQL over the existing ``tracks`` table — no schema of its own beyond the
``subsonic_credentials`` table in ``base.py``. Albums have no table: an album is
the ``(album, COALESCE(album_artist, ''))`` group, the same grouping
``list_albums`` uses, so the Subsonic view and the web UI never disagree about
what an album is.
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

_ALBUM_SELECT = f"""
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
    WHERE {_LIVE} AND COALESCE(album, '') != ''
"""
_ALBUM_GROUP = " GROUP BY album, COALESCE(album_artist, '')"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    def get_track_by_id(self, track_id: int) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(f"SELECT * FROM tracks WHERE id = ? AND {_LIVE}",
                               (track_id,)).fetchone()
        return dict(row) if row else None

    def subsonic_search_songs(self, query: str, count: int, offset: int) -> list[dict]:
        """Songs whose title, artist or album contains ``query``. An empty query
        pages through the whole library — clients such as Symfonium sync their
        local index that way."""
        sql = f"SELECT * FROM tracks WHERE {_LIVE}"
        params: list = []
        if query:
            like = f"%{query}%"
            sql += " AND (title LIKE ? OR artist LIKE ? OR album LIKE ?)"
            params += [like, like, like]
        sql += " ORDER BY id LIMIT ? OFFSET ?"
        with self._connect() as conn:
            rows = conn.execute(sql, params + [count, offset]).fetchall()
        return [dict(r) for r in rows]

    def subsonic_random_songs(self, size: int, from_year: Optional[int] = None,
                              to_year: Optional[int] = None) -> list[dict]:
        sql = f"SELECT * FROM tracks WHERE {_LIVE} AND status = 'done'"
        params: list = []
        if from_year is not None:
            sql += " AND year >= ?"
            params.append(from_year)
        if to_year is not None:
            sql += " AND year <= ?"
            params.append(to_year)
        with self._connect() as conn:
            rows = conn.execute(sql + " ORDER BY RANDOM() LIMIT ?", params + [size]).fetchall()
        return [dict(r) for r in rows]

    def subsonic_starred_songs(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM tracks WHERE {_LIVE} AND starred = 1 "
                "ORDER BY artist COLLATE NOCASE, album COLLATE NOCASE, disc_no, track_no"
            ).fetchall()
        return [dict(r) for r in rows]

    def count_live_tracks(self) -> int:
        with self._connect() as conn:
            return conn.execute(f"SELECT COUNT(*) FROM tracks WHERE {_LIVE}").fetchone()[0]

    # ── Albums ───────────────────────────────────────────────────────────────
    def subsonic_album_keys(self) -> list[tuple[str, str]]:
        """Every (album, album_artist) group — the input for the id map."""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT album, COALESCE(album_artist, '') AS aa FROM tracks "
                f"WHERE {_LIVE} AND COALESCE(album, '') != ''").fetchall()
        return [(r["album"], r["aa"]) for r in rows]

    def subsonic_albums(self, order: str = "alphabeticalByName", limit: int = 10,
                        offset: int = 0, from_year: Optional[int] = None,
                        to_year: Optional[int] = None,
                        keys: Optional[Iterable[tuple[str, str]]] = None) -> list[dict]:
        """Album aggregates, ordered per ``ALBUM_ORDERS`` (unknown → by name).

        ``keys`` restricts to specific (album, album_artist) groups (an artist's
        albums, search hits). ``order='starred'`` keeps albums with any starred
        track; ``byYear`` with from > to sorts newest first, as the spec asks."""
        order_sql = ALBUM_ORDERS.get(order, ALBUM_ORDERS["alphabeticalByName"])
        where, params = "", []
        if keys is not None:
            keys = list(keys)
            if not keys:
                return []
            where += " AND (" + " OR ".join(
                ["(album = ? AND COALESCE(album_artist, '') = ?)"] * len(keys)) + ")"
            for a, aa in keys:
                params += [a, aa]
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
        sql = (_ALBUM_SELECT + where + _ALBUM_GROUP + having +
               f" ORDER BY {order_sql} LIMIT ? OFFSET ?")
        with self._connect() as conn:
            rows = conn.execute(sql, params + [limit, offset]).fetchall()
        return [dict(r) for r in rows]

    def subsonic_album_tracks(self, keys: Iterable[tuple[str, str]]) -> list[dict]:
        keys = list(keys)
        if not keys:
            return []
        where = " OR ".join(["(album = ? AND COALESCE(album_artist, '') = ?)"] * len(keys))
        params = [v for k in keys for v in k]
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM tracks WHERE {_LIVE} AND ({where}) "
                "ORDER BY disc_no, track_no, file_path", params).fetchall()
        return [dict(r) for r in rows]

    def subsonic_search_album_keys(self, query: str, count: int, offset: int) -> list[tuple[str, str]]:
        sql = (f"SELECT album, COALESCE(album_artist, '') AS aa FROM tracks "
               f"WHERE {_LIVE} AND COALESCE(album, '') != ''")
        params: list = []
        if query:
            sql += " AND (album LIKE ? OR album_artist LIKE ?)"
            params += [f"%{query}%", f"%{query}%"]
        sql += (" GROUP BY album, COALESCE(album_artist, '') "
                "ORDER BY album COLLATE NOCASE LIMIT ? OFFSET ?")
        with self._connect() as conn:
            rows = conn.execute(sql, params + [count, offset]).fetchall()
        return [(r["album"], r["aa"]) for r in rows]
