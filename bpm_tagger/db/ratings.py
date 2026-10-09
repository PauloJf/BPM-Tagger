"""Per-account ratings (1-5 stars) and dislikes.

Rows live in ``track_ratings`` keyed by (owner, track_id), owner being the
session_owner key — ``'admin'`` or ``'player:<id>'``. The shared guest login
never rates. A row with no rating and no dislike is deleted, not kept.

The star is derived: starred <=> rating >= STAR_MIN. For the admin, every write
also updates ``tracks.starred`` / ``tracks.disliked`` in the same transaction,
so the library-wide readers (Navidrome star sync, suggestions seeding, the
Subsonic album index, Cadence) keep reading the admin's view unchanged.
See docs/plans/ratings-weighted-picking.md.
"""

from datetime import datetime, timezone
from typing import Iterable, Optional

ADMIN_OWNER = "admin"
GUEST_OWNER = "guest"
STAR_MIN = 4          # rating at/above which a track counts as starred
STAR_ON = 4           # a star on an unrated / < 4 track sets this rating
STAR_OFF = 3          # an unstar on a >= 4 track drops it to this


# Seeds the admin's rows from the tracks.starred / tracks.disliked projection:
# the one-time migration (db/base.py) and tests that seed those flags directly.
SEED_ADMIN_FROM_PROJECTION_SQL = (
    "INSERT OR REPLACE INTO track_ratings (owner, track_id, rating, disliked, updated_at) "
    "SELECT 'admin', id, CASE WHEN starred = 1 THEN 4 END, COALESCE(disliked, 0), "
    "datetime('now') FROM tracks WHERE starred = 1 OR disliked = 1")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_rating(value) -> Optional[int]:
    """1..5 as an int, or None (unrated) for None / 0 / "". Raises ValueError
    for anything else, so API handlers can answer 400."""
    if value is None or value == "" or value == 0:
        return None
    if isinstance(value, bool):
        raise ValueError("rating must be 1-5 or null")
    r = int(value)
    if not 1 <= r <= 5:
        raise ValueError("rating must be 1-5 or null")
    return r


def star_to_rating(current: Optional[int], starred: bool) -> Optional[int]:
    """The rating a star / unstar implies, given the current rating (D1)."""
    if starred:
        return current if current is not None and current >= STAR_MIN else STAR_ON
    if current is not None and current >= STAR_MIN:
        return STAR_OFF
    return current


def not_disliked_sql(alias: str = "t") -> str:
    """WHERE fragment dropping tracks the owner (one ``?`` param) disliked."""
    return (f"NOT EXISTS (SELECT 1 FROM track_ratings r_x WHERE r_x.track_id = {alias}.id "
            "AND r_x.owner = ? AND r_x.disliked = 1)")


class RatingsMixin:

    # ── internals (take an open connection) ──────────────────────────────────
    def _track_id(self, conn, file_path: str) -> Optional[int]:
        row = conn.execute("SELECT id FROM tracks WHERE file_path = ?", (file_path,)).fetchone()
        return row["id"] if row else None

    def _read_mark(self, conn, owner: str, track_id: int) -> tuple[Optional[int], bool]:
        row = conn.execute(
            "SELECT rating, disliked FROM track_ratings WHERE owner = ? AND track_id = ?",
            (owner, track_id)).fetchone()
        if not row:
            return None, False
        return row["rating"], bool(row["disliked"])

    def _write_mark(self, conn, owner: str, track_id: int,
                    rating: Optional[int], disliked: bool) -> None:
        if rating is None and not disliked:
            # Keep rating_base alive for the admin: it's the Navidrome sync
            # baseline and must survive a local clear until the next sync.
            if owner == ADMIN_OWNER and conn.execute(
                    "SELECT 1 FROM track_ratings WHERE owner = ? AND track_id = ? "
                    "AND rating_base IS NOT NULL", (owner, track_id)).fetchone():
                conn.execute(
                    "UPDATE track_ratings SET rating = NULL, disliked = 0, updated_at = ? "
                    "WHERE owner = ? AND track_id = ?", (_now(), owner, track_id))
            else:
                conn.execute("DELETE FROM track_ratings WHERE owner = ? AND track_id = ?",
                             (owner, track_id))
        else:
            conn.execute(
                "INSERT INTO track_ratings (owner, track_id, rating, disliked, updated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(owner, track_id) DO UPDATE SET "
                "rating = excluded.rating, disliked = excluded.disliked, "
                "updated_at = excluded.updated_at",
                (owner, track_id, rating, 1 if disliked else 0, _now()))
        if owner == ADMIN_OWNER:
            conn.execute(
                "UPDATE tracks SET starred = ?, disliked = ? WHERE id = ?",
                (1 if rating is not None and rating >= STAR_MIN else 0,
                 1 if disliked else 0, track_id))

    def _mark_result(self, rating: Optional[int], disliked: bool) -> dict:
        return {"rating": rating, "disliked": disliked,
                "starred": rating is not None and rating >= STAR_MIN}

    # ── public API ───────────────────────────────────────────────────────────
    def get_mark(self, owner: str, file_path: str) -> dict:
        """{rating, disliked, starred} of one track for one account."""
        with self._connect() as conn:
            tid = self._track_id(conn, file_path)
            rating, disliked = self._read_mark(conn, owner, tid) if tid else (None, False)
        return self._mark_result(rating, disliked)

    def set_rating(self, owner: str, file_path: str, rating: Optional[int]) -> Optional[dict]:
        """Set (1-5) or clear (None) an account's rating. Keeps the dislike.
        Returns the new mark, or None when the track doesn't exist."""
        rating = normalize_rating(rating)
        with self._connect() as conn:
            tid = self._track_id(conn, file_path)
            if tid is None:
                return None
            _, disliked = self._read_mark(conn, owner, tid)
            self._write_mark(conn, owner, tid, rating, disliked)
        return self._mark_result(rating, disliked)

    def set_owner_disliked(self, owner: str, file_path: str, disliked: bool) -> Optional[dict]:
        """Set / clear an account's dislike. Keeps the rating (D3)."""
        with self._connect() as conn:
            tid = self._track_id(conn, file_path)
            if tid is None:
                return None
            rating, _ = self._read_mark(conn, owner, tid)
            self._write_mark(conn, owner, tid, rating, bool(disliked))
        return self._mark_result(rating, bool(disliked))

    def set_owner_starred(self, owner: str, file_path: str, starred: bool) -> Optional[dict]:
        """Star / unstar through the rating (D1): star -> at least 4, unstar a
        >= 4 track -> 3. Used by the compat /api/track/star, Subsonic star and
        Navidrome star sync."""
        with self._connect() as conn:
            tid = self._track_id(conn, file_path)
            if tid is None:
                return None
            rating, disliked = self._read_mark(conn, owner, tid)
            rating = star_to_rating(rating, bool(starred))
            self._write_mark(conn, owner, tid, rating, disliked)
        return self._mark_result(rating, disliked)

    def owner_marks(self, owner: str) -> dict[str, tuple[Optional[int], bool]]:
        """{file_path: (rating, disliked)} for every track the account has marked."""
        if owner == GUEST_OWNER:
            return {}
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT t.file_path, r.rating, r.disliked FROM track_ratings r "
                "JOIN tracks t ON t.id = r.track_id WHERE r.owner = ? "
                "AND (r.rating IS NOT NULL OR r.disliked = 1)", (owner,)).fetchall()
        return {r["file_path"]: (r["rating"], bool(r["disliked"])) for r in rows}

    def owner_played_paths(self, owner: str) -> set:
        """File paths a player account has played (its play_events). Not used
        for the admin, whose "played" is the global tracks.play_count."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT file_path FROM play_events WHERE owner = ?", (owner,)).fetchall()
        return {r["file_path"] for r in rows}

    def annotate_marks(self, rows: Iterable[dict], owner: str) -> list[dict]:
        """Add the account's ``rating``, ``disliked``, ``starred`` and ``is_new``
        to track dicts (each needs ``file_path``; the admin's ``is_new`` also
        reads ``play_count``). "New" = unrated and unplayed by this account
        (D9); the guest has no ratings and nothing is ever new for it (D6).
        Mutates and returns the rows as a list."""
        rows = list(rows)
        marks = self.owner_marks(owner)
        played = None
        if owner not in (ADMIN_OWNER, GUEST_OWNER):
            played = self.owner_played_paths(owner)
        for t in rows:
            rating, disliked = marks.get(t["file_path"], (None, False))
            t["rating"] = rating
            t["disliked"] = disliked
            t["starred"] = rating is not None and rating >= STAR_MIN
            if owner == GUEST_OWNER or rating is not None:
                t["is_new"] = False
            elif played is None:
                t["is_new"] = not (t.get("play_count") or 0)
            else:
                t["is_new"] = t["file_path"] not in played
        return rows

    def rating_distribution(self, owner: str) -> dict:
        """Counts for the Stats card: {'1'..'5', 'unrated', 'disliked', 'total'}
        over non-deleted tracks. Disliked tracks still count at their rating."""
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM tracks WHERE status != 'deleted'").fetchone()[0]
            rows = conn.execute(
                "SELECT r.rating, SUM(r.disliked) AS dis, COUNT(*) AS n FROM track_ratings r "
                "JOIN tracks t ON t.id = r.track_id WHERE r.owner = ? AND t.status != 'deleted' "
                "GROUP BY r.rating", (owner,)).fetchall()
        out = {str(i): 0 for i in range(1, 6)}
        rated = disliked = 0
        for r in rows:
            disliked += int(r["dis"] or 0)
            if r["rating"] is not None:
                out[str(r["rating"])] = int(r["n"])
                rated += int(r["n"])
        out.update(unrated=int(total) - rated, disliked=disliked, total=int(total))
        return out

    def count_new_for_owner(self, owner: str) -> int:
        """How many non-deleted library tracks are "new" (D9) for this account —
        unrated and unplayed by it. Same rule as ``annotate_marks``' ``is_new``,
        as a single COUNT so the Settings pick-preview doesn't have to pull
        every track down to compute it. The guest never rates or has play
        history (D6), so nothing is ever "new" for it."""
        if owner == GUEST_OWNER:
            return 0
        unrated = ("NOT EXISTS (SELECT 1 FROM track_ratings r WHERE r.track_id = t.id "
                   "AND r.owner = ? AND r.rating IS NOT NULL)")
        if owner == ADMIN_OWNER:
            where = unrated + " AND COALESCE(t.play_count, 0) = 0"
            params = (owner,)
        else:
            where = (unrated + " AND NOT EXISTS (SELECT 1 FROM play_events pe "
                     "WHERE pe.owner = ? AND pe.file_path = t.file_path)")
            params = (owner, owner)
        with self._connect() as conn:
            return conn.execute(
                f"SELECT COUNT(*) FROM tracks t WHERE t.status != 'deleted' AND {where}",
                params).fetchone()[0]

    def delete_owner_marks(self, owner: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM track_ratings WHERE owner = ?", (owner,))

    # ── Navidrome rating sync (admin only; see integrations/rating_sync.py) ───
    def all_admin_ratings_for_sync(self) -> list[dict]:
        """Every non-deleted track with the fields the Navidrome rating-sync
        driver needs: the admin's rating, the last-synced baseline
        (``rating_base``), the cached Subsonic id, and the metadata/norm
        columns ``_match_remote_to_local`` (star_sync) uses to resolve a
        remote song. A track with no ``track_ratings`` row at all (never
        rated, never synced) still comes back with ``rating`` / ``rating_base``
        both None via the LEFT JOIN."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT t.file_path, r.rating, r.rating_base, t.nd_song_id, "
                "t.title, t.artist, t.album, t.duration_ms, t.isrc, "
                "t.norm_title, t.norm_artist FROM tracks t "
                "LEFT JOIN track_ratings r ON r.track_id = t.id AND r.owner = ? "
                "WHERE t.status != 'deleted'", (ADMIN_OWNER,)).fetchall()
        return [dict(r) for r in rows]

    def set_rating_synced(self, file_path: str, rating: Optional[int],
                          nd_song_id: str | None = None) -> None:
        """Write the reconciled admin rating and advance the Navidrome sync
        baseline (``rating_base``) in lockstep, mirroring ``set_star_synced``.
        Caller advances the baseline ONLY after any required remote write
        succeeded, so a failed push retries next run. Updates ``nd_song_id``
        on ``tracks`` when a fresh id was resolved (never clears a cached id
        with None)."""
        rating = normalize_rating(rating)
        # Keeps the star projection (tracks.starred) in step, same as a plain
        # rating write — rating_base is the only thing that needs a direct
        # write on top of what set_rating already did.
        self.set_rating(ADMIN_OWNER, file_path, rating)
        with self._connect() as conn:
            tid = self._track_id(conn, file_path)
            if tid is None:
                return
            conn.execute(
                "INSERT INTO track_ratings (owner, track_id, rating, disliked, "
                "rating_base, updated_at) VALUES (?, ?, ?, 0, ?, ?) "
                "ON CONFLICT(owner, track_id) DO UPDATE SET "
                "rating_base = excluded.rating_base, updated_at = excluded.updated_at",
                (ADMIN_OWNER, tid, rating, rating, _now()))
            if nd_song_id is not None:
                conn.execute("UPDATE tracks SET nd_song_id = ? WHERE id = ?",
                             (nd_song_id, tid))
