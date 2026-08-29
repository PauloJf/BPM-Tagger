"""Track index, library browse, stats, run candidates, lyrics/ISRC, duplicates."""

import os

from datetime import datetime, timezone
from typing import Optional

from ..bpm.tags import get_file_hash
from ..grabber.matching import normalize_artist_name, split_artist_credits
from .constants import TRACK_SORTS

def _dupe_signature(paths) -> str:
    """Stable signature for a duplicate group (its sorted, unique file paths)."""
    return "\n".join(sorted(set(paths)))

class TracksMixin:
    _SUSPICIOUS_WHERE = """status = 'done' AND locked = 0 AND reviewed = 0 AND (
        needs_review = 1
        OR (bpm_confidence IS NOT NULL AND bpm_confidence < ?)
        OR detector = 'librosa'
        OR (bpm IS NOT NULL AND (bpm < ? OR bpm > ?))
    )"""

    _SUSPICIOUS_COLS = (
        "file_path, bpm, bpm_dr, bpm_es, bpm_lb, bpm_confidence, detector, needs_review"
    )

    def get_all_file_hashes(self) -> dict:
        """Return {file_path: (file_hash, status, locked)} in one query for bulk filtering."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT file_path, file_hash, status, locked FROM tracks"
            ).fetchall()
        return {r["file_path"]: (r["file_hash"], r["status"], bool(r["locked"])) for r in rows}

    def get_track(self, file_path: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tracks WHERE file_path = ?", (file_path,)).fetchone()
            return dict(row) if row else None

    def upsert_track(self, file_path: str, file_hash: str,
                     bpm: Optional[float], bpm_dr: Optional[float],
                     bpm_es: Optional[float], bpm_lb: Optional[float],
                     confidence: Optional[float], detector: Optional[str],
                     status: str, needs_review: bool = False, error: Optional[str] = None,
                     waveform_peaks: Optional[str] = None,
                     loudness_lufs: Optional[float] = None,
                     loudness_source: Optional[str] = None):
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO tracks
                    (file_path, file_hash, bpm, bpm_dr, bpm_es, bpm_lb, bpm_confidence,
                     detector, analyzed_at, status, needs_review, error_message, waveform_peaks,
                     loudness_lufs, loudness_source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    file_hash      = excluded.file_hash,
                    bpm            = excluded.bpm,
                    bpm_dr         = excluded.bpm_dr,
                    bpm_es         = excluded.bpm_es,
                    bpm_lb         = excluded.bpm_lb,
                    bpm_confidence = excluded.bpm_confidence,
                    detector       = excluded.detector,
                    analyzed_at    = excluded.analyzed_at,
                    status         = excluded.status,
                    needs_review   = excluded.needs_review,
                    reviewed       = 0,
                    error_message  = excluded.error_message,
                    waveform_peaks = COALESCE(excluded.waveform_peaks, waveform_peaks),
                    -- Same COALESCE treatment as the waveform: an error pass (or a
                    -- scan with measurement off) must not wipe a good measurement.
                    loudness_lufs   = COALESCE(excluded.loudness_lufs, loudness_lufs),
                    loudness_source = COALESCE(excluded.loudness_source, loudness_source)
            """, (file_path, file_hash, bpm, bpm_dr, bpm_es, bpm_lb, confidence,
                  detector, now, status, int(needs_review), error, waveform_peaks,
                  loudness_lufs, loudness_source))
            conn.commit()

    def lock_track(self, file_path: str, bpm: Optional[float]):
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO tracks (file_path, bpm, analyzed_at, status, locked)
                VALUES (?, ?, ?, 'done', 1)
                ON CONFLICT(file_path) DO UPDATE SET
                    bpm         = COALESCE(excluded.bpm, bpm),
                    analyzed_at = excluded.analyzed_at,
                    status      = 'done',
                    needs_review = 0,
                    reviewed    = 1,
                    locked      = 1
            """, (file_path, bpm, now))
            conn.commit()

    def unlock_track(self, file_path: str):
        with self._connect() as conn:
            conn.execute("UPDATE tracks SET locked = 0 WHERE file_path = ?", (file_path,))
            conn.commit()

    def refresh_hashes(self) -> tuple[int, int]:
        """Recompute size:mtime for every done/locked track that exists on disk.

        Returns (updated, missing) counts. Tracks whose files are gone are left
        untouched so they show up normally on the next scan.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT file_path, file_hash FROM tracks WHERE status = 'done' OR locked = 1"
            ).fetchall()

        # Do all the filesystem stat work with NO transaction open, then apply the
        # changed hashes in one short write. Holding the WAL write lock across
        # thousands of stat calls would block every other writer meanwhile.
        missing = 0
        changed: list[tuple[str, str]] = []
        for row in rows:
            fp = row["file_path"]
            if not os.path.exists(fp):
                missing += 1
                continue
            new_hash = get_file_hash(fp)
            if new_hash != row["file_hash"]:
                changed.append((new_hash, fp))

        if changed:
            with self._connect() as conn:
                conn.executemany(
                    "UPDATE tracks SET file_hash = ? WHERE file_path = ?", changed)
                conn.commit()
        return len(changed), missing

    def approve_track(self, file_path: str) -> None:
        """Clear needs_review and mark as reviewed without changing BPM or locking."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE tracks SET needs_review = 0, reviewed = 1 WHERE file_path = ?",
                (file_path,)
            )
            conn.commit()

    def get_error_tracks(self) -> list[str]:
        """Return file paths of unlocked tracks with status='error'."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT file_path FROM tracks WHERE status = 'error' AND locked = 0"
            ).fetchall()
        return [r["file_path"] for r in rows]

    def save_waveform_peaks(self, file_path: str, waveform_peaks_json: str) -> None:
        """Back-fill waveform_peaks for a track that was processed before this column existed."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE tracks SET waveform_peaks = ? WHERE file_path = ?",
                (waveform_peaks_json, file_path),
            )
            conn.commit()

    def save_loudness(self, file_path: str, lufs: Optional[float],
                      source: Optional[str]) -> None:
        """Back-fill loudness for a track analyzed before this column existed (or
        before measurement was switched on). Unlike upsert_track this writes
        unconditionally, so a re-measure can replace a 'tag'-sourced estimate."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE tracks SET loudness_lufs = ?, loudness_source = ? WHERE file_path = ?",
                (lufs, source, file_path),
            )
            conn.commit()

    def count_unmeasured_loudness(self) -> int:
        """Live tracks with no loudness value yet — drives the backfill progress UI."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM tracks "
                "WHERE status != 'deleted' AND loudness_lufs IS NULL"
            ).fetchone()[0]

    def get_unmeasured_loudness_paths(self, limit: int) -> list[str]:
        """Paths of live tracks still missing a loudness value, oldest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT file_path FROM tracks "
                "WHERE status != 'deleted' AND loudness_lufs IS NULL "
                "ORDER BY analyzed_at LIMIT ?", (limit,)
            ).fetchall()
        return [r["file_path"] for r in rows]

    def needs_analysis(self, file_path: str, file_hash: str) -> bool:
        track = self.get_track(file_path)
        if not track:
            return True
        if track["locked"]:
            return False
        if track["status"] != "done":
            return True
        return track["file_hash"] != file_hash

    @staticmethod
    def _tracks_filter(q: str, filter: str,
                       bpm_target: Optional[float], bpm_tol: float,
                       bpm_cadence: bool = False) -> tuple[str, list]:
        """Build the shared WHERE clause + params for the library listing views
        (paged rows and the full path list). Kept in one place so Play All /
        Shuffle queue exactly the same set the table shows.

        With `bpm_cadence`, a BPM target also matches half- and double-time tracks
        (e.g. a 170 SPM running cadence also matches 85 BPM songs) — you can step
        to those at half/double tempo."""
        clauses: list[str] = []
        params: list = []
        if filter == "deleted":
            clauses.append("status = 'deleted'")
        else:
            clauses.append("status != 'deleted'")
            fc = {
                "review": "needs_review = 1 AND locked = 0 AND reviewed = 0",
                "locked": "locked = 1",
                "no_isrc": "(isrc IS NULL OR isrc = '')",
                "starred": "starred = 1",
                "disliked": "disliked = 1",
                "unplaylisted": (
                    "NOT EXISTS (SELECT 1 FROM playlist_tracks pt "
                    "WHERE pt.matched_file_path = tracks.file_path "
                    "AND pt.removed_at IS NULL)"),
            }.get(filter, "")
            if fc:
                clauses.append(fc)
        if q:
            like = f"%{q}%"
            clauses.append("(file_path LIKE ? OR title LIKE ? OR artist LIKE ? OR album LIKE ?)")
            params.extend([like, like, like, like])
        if bpm_target is not None:
            lo, hi = bpm_target - bpm_tol, bpm_target + bpm_tol
            if bpm_cadence:
                clauses.append("bpm IS NOT NULL AND (bpm BETWEEN ? AND ? "
                               "OR bpm BETWEEN ? AND ? OR bpm BETWEEN ? AND ?)")
                params.extend([lo, hi, lo / 2, hi / 2, lo * 2, hi * 2])
            else:
                clauses.append("bpm IS NOT NULL AND bpm BETWEEN ? AND ?")
                params.extend([lo, hi])
        return "WHERE " + " AND ".join(clauses), params

    @staticmethod
    def _tracks_order(sort: str) -> str:
        """The ORDER BY for a listing sort key, falling back to the default for
        anything unknown — the listing views take ?sort= straight from the URL."""
        return TRACK_SORTS.get(sort or "", TRACK_SORTS[""])

    def get_tracks_page(self, q: str, limit: int, offset: int,
                        filter: str = "",
                        bpm_target: Optional[float] = None,
                        bpm_tol: float = 5.0, bpm_cadence: bool = False,
                        sort: str = "") -> tuple[list[dict], int]:
        where, params = self._tracks_filter(q, filter, bpm_target, bpm_tol, bpm_cadence)
        order = self._tracks_order(sort)
        with self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM tracks {where}", params
            ).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM tracks {where} ORDER BY {order} LIMIT ? OFFSET ?",
                params + [limit, offset]
            ).fetchall()
        return [dict(r) for r in rows], total

    def get_track_paths(self, q: str = "", filter: str = "",
                        bpm_target: Optional[float] = None, bpm_tol: float = 5.0,
                        bpm_cadence: bool = False, limit: int = 5000,
                        sort: str = "") -> list[dict]:
        """Ordered (file_path, title, artist) for every track matching the current
        filter — feeds the player's Play All / Shuffle. Same ordering as the
        table (hence the shared `sort`); capped so a huge library can't build a
        pathological queue."""
        where, params = self._tracks_filter(q, filter, bpm_target, bpm_tol, bpm_cadence)
        order = self._tracks_order(sort)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT file_path, title, artist, loudness_lufs FROM tracks {where} "
                f"ORDER BY {order} LIMIT ?",
                params + [limit]
            ).fetchall()
        return [dict(r) for r in rows]

    def set_starred(self, file_path: str, starred: bool):
        with self._connect() as conn:
            conn.execute("UPDATE tracks SET starred = ? WHERE file_path = ?",
                         (1 if starred else 0, file_path))

    def set_disliked(self, file_path: str, disliked: bool):
        with self._connect() as conn:
            conn.execute("UPDATE tracks SET disliked = ? WHERE file_path = ?",
                         (1 if disliked else 0, file_path))

    def all_tracks_for_star_sync(self) -> list[dict]:
        """Every non-deleted track with the fields the Navidrome star-sync driver
        needs: local star state, the last-synced baseline, the cached Subsonic id,
        and the metadata/norm columns used to resolve a remote song id."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT file_path, starred, starred_base, nd_song_id, "
                "title, artist, album, duration_ms, isrc, norm_title, norm_artist "
                "FROM tracks WHERE status != 'deleted'"
            ).fetchall()
        return [dict(r) for r in rows]

    def set_star_synced(self, file_path: str, starred: bool, nd_song_id: str | None = None):
        """Write the reconciled star state and advance the sync baseline in lockstep.
        Caller advances the baseline ONLY after any required remote write succeeded,
        so a failed push retries on the next run. Updates nd_song_id when a fresh id
        was resolved (never clears a cached id with None)."""
        with self._connect() as conn:
            if nd_song_id is not None:
                conn.execute(
                    "UPDATE tracks SET starred = ?, starred_base = ?, nd_song_id = ? "
                    "WHERE file_path = ?",
                    (1 if starred else 0, 1 if starred else 0, nd_song_id, file_path))
            else:
                conn.execute(
                    "UPDATE tracks SET starred = ?, starred_base = ? WHERE file_path = ?",
                    (1 if starred else 0, 1 if starred else 0, file_path))

    def set_play_counts(self, updates: list[tuple]) -> int:
        """Bulk-write pulled play data: (file_path, play_count, last_played,
        nd_song_id) tuples. Merges rather than overwrites — play_count takes the
        MAX of the local tally and the remote count, so a pull never discards
        plays counted locally (e.g. while Navidrome was disconnected) yet still
        picks up higher remote totals (other devices). A remote play we forwarded
        is already in our local count, so MAX also avoids double-counting. Warms
        the star sync's id cache — never clears a cached id with None. Returns the
        number of rows written."""
        if not updates:
            return 0
        with self._connect() as conn:
            conn.executemany(
                "UPDATE tracks SET play_count = MAX(COALESCE(play_count, 0), ?), "
                "last_played = ?, nd_song_id = COALESCE(?, nd_song_id) WHERE file_path = ?",
                [(pc, lp, sid, path) for (path, pc, lp, sid) in updates])
            conn.commit()
        return len(updates)

    def set_nd_song_id(self, file_path: str, nd_song_id: str | None) -> None:
        """Cache the resolved Navidrome song id without touching the play count."""
        if not nd_song_id:
            return
        with self._connect() as conn:
            conn.execute("UPDATE tracks SET nd_song_id = ? WHERE file_path = ?",
                         (nd_song_id, file_path))
            conn.commit()

    def bump_play_count(self, file_path: str, nd_song_id: str | None = None):
        """+1 the local play count for every play, independent of Navidrome, so
        counts work offline and persist while it's disconnected. A later pull
        merges in the remote total with MAX (see set_play_counts), so this is
        never double-counted. Caches the song id when given."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE tracks SET play_count = COALESCE(play_count, 0) + 1, "
                "nd_song_id = COALESCE(?, nd_song_id) WHERE file_path = ?",
                (nd_song_id, file_path))

    @staticmethod
    def _valid_run_stat_key(key: str) -> bool:
        """Guard the open-ended key/value store: lowercase/digits/underscore only,
        bounded length. Keeps a broken or hostile client from writing arbitrary
        keys (e.g. cadence bins are dynamic — cad_120, cad_150 — so we can't use a
        fixed allowlist)."""
        return (
            isinstance(key, str) and 1 <= len(key) <= 32
            and all(c.islower() or c.isdigit() or c == "_" for c in key)
        )

    def _clean_run_deltas(self, deltas: dict) -> list[tuple[str, float]]:
        """The reportable (key, value) pairs of a client batch. Invalid keys and
        non-finite / negative values are dropped rather than rejecting the whole
        batch; a single batch's delta is capped so one bad report can't balloon
        a total (24 h of ms is a generous per-flush ceiling)."""
        import math
        clean: list[tuple[str, float]] = []
        for key, raw in (deltas or {}).items():
            if not self._valid_run_stat_key(key):
                continue
            try:
                val = float(raw)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(val) or val < 0:
                continue
            clean.append((key, min(val, 86_400_000.0)))
        return clean

    def add_run_stats(self, deltas: dict, owner: str | None = None,
                      run: dict | None = None) -> int | None:
        """Increment cumulative run-mode counters by the client-reported deltas
        (wall_ms, shifted_ms, native_ms, cadence_weighted, tracks_played, and
        per-cadence-bin cad_<bpm> buckets).

        The global ``run_stats`` totals behave exactly as they always have — they
        stay all-time and account-blind, so the Stats page's numbers never shift
        under an upgrade. When ``owner`` is given the same batch is ALSO mirrored
        into ``run_stats_owner`` (per-account totals), and when ``run`` context
        (``{source, target}``) rides along it is folded into that owner's run
        journal row (see db/runs.py) — one transaction, one code path, so the
        three views can never disagree about a batch.

        Returns the journal run id when the batch was attributed to a run."""
        clean = self._clean_run_deltas(deltas)
        if not clean:
            return None
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO run_stats (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = value + excluded.value",
                clean,
            )
            if not owner:
                return None
            conn.executemany(
                "INSERT INTO run_stats_owner (owner, key, value) VALUES (?, ?, ?) "
                "ON CONFLICT(owner, key) DO UPDATE SET value = value + excluded.value",
                [(owner, k, v) for k, v in clean],
            )
            if run is None:
                return None
            return self._apply_run_event(conn, owner, dict(clean), run)

    def get_run_stats(self) -> dict:
        """All cumulative run-mode counters as a {key: value} dict (empty before
        the first run)."""
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM run_stats").fetchall()
        return {r["key"]: r["value"] for r in rows}

    # ── Play-count leaderboards (Navidrome-pulled; empty until a play sync) ────
    def get_top_tracks(self, limit: int = 10, offset: int = 0) -> list[dict]:
        """Most-played library tracks, with album/artist so the Stats page can
        link to the track, album and artist pages. Offset pages through the
        leaderboard (the ORDER BY is fully deterministic, so pages never skip
        or repeat rows)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT file_path, title, artist, album, album_artist, bpm, play_count "
                "FROM tracks WHERE status != 'deleted' AND COALESCE(play_count, 0) > 0 "
                "ORDER BY play_count DESC, title COLLATE NOCASE, file_path LIMIT ? OFFSET ?",
                (limit, offset)).fetchall()
        return [dict(r) for r in rows]

    def get_top_artists(self, limit: int = 10, offset: int = 0) -> list[dict]:
        """Most-played artists by summed play count. Groups via track_artists
        (each credited artist gets credit for the track's plays), matching the
        artist index so the `name` links straight to the artist page."""
        with self._connect() as conn:
            rows = conn.execute("""
                WITH live AS (
                    SELECT ta.norm_name, ta.name, t.play_count
                    FROM track_artists ta JOIN tracks t ON t.id = ta.track_id
                    WHERE t.status != 'deleted'
                ),
                display AS (
                    SELECT norm_name, name FROM (
                        SELECT norm_name, name,
                               ROW_NUMBER() OVER (
                                   PARTITION BY norm_name
                                   ORDER BY COUNT(*) DESC, name COLLATE NOCASE
                               ) AS rn
                        FROM live GROUP BY norm_name, name
                    ) WHERE rn = 1
                )
                SELECT d.name AS name,
                       SUM(COALESCE(l.play_count, 0)) AS plays,
                       COUNT(*) AS tracks
                FROM live l JOIN display d ON d.norm_name = l.norm_name
                WHERE COALESCE(l.play_count, 0) > 0
                GROUP BY l.norm_name
                ORDER BY plays DESC, name COLLATE NOCASE LIMIT ? OFFSET ?
            """, (limit, offset)).fetchall()
        return [dict(r) for r in rows]

    def get_total_plays(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT SUM(COALESCE(play_count, 0)) FROM tracks WHERE status != 'deleted'"
            ).fetchone()
        return int(row[0]) if row and row[0] else 0

    # Runnable rows of a playlist: matched local files (non-tombstone, 'have')
    # joined to analyzed, non-deleted, non-disliked tracks. Deduped by file_path
    # (two source tracks can resolve to one local file). Shared by the run-queue
    # builder and its per-playlist availability count.
    _PLAYLIST_RUN_JOIN = (
        "FROM playlist_tracks pt JOIN tracks t ON t.file_path = pt.matched_file_path "
        "WHERE pt.playlist_id = ? AND pt.removed_at IS NULL AND pt.match_status = 'have' "
        "AND t.status != 'deleted' AND t.bpm IS NOT NULL "
        "AND (t.disliked IS NULL OR t.disliked = 0)"
    )

    def get_run_candidates(self, playlist_id: Optional[int] = None) -> list[dict]:
        """Analyzed, non-deleted, non-disliked tracks feeding the run-queue builder
        (which octave-folds and scores in Python, cheap even at library scale).
        Disliked tracks are dropped here so they never surface in a run.

        With ``playlist_id`` the pool is restricted to that playlist's matched local
        tracks (Phase 3) instead of the whole library."""
        with self._connect() as conn:
            if playlist_id is None:
                rows = conn.execute(
                    "SELECT file_path, title, artist, bpm, starred, play_count, loudness_lufs FROM tracks "
                    "WHERE status != 'deleted' AND bpm IS NOT NULL "
                    "AND (disliked IS NULL OR disliked = 0)"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT t.file_path, t.title, t.artist, t.bpm, t.starred, t.play_count, t.loudness_lufs "
                    + self._PLAYLIST_RUN_JOIN + " GROUP BY t.file_path", (playlist_id,)
                ).fetchall()
        return [dict(r) for r in rows]

    def get_run_candidates_for_playlists(self, playlist_ids) -> list[dict]:
        """Run candidates pooled across several playlists — the union of a scoped
        player's assigned playlists, deduped by file_path. Used as the top-up pool
        for a scoped player so a thin playlist never pads from the whole library.
        An empty set of ids yields an empty pool."""
        ids = list(dict.fromkeys(int(p) for p in playlist_ids))
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        join = (
            "FROM playlist_tracks pt JOIN tracks t ON t.file_path = pt.matched_file_path "
            f"WHERE pt.playlist_id IN ({placeholders}) AND pt.removed_at IS NULL "
            "AND pt.match_status = 'have' AND t.status != 'deleted' AND t.bpm IS NOT NULL "
            "AND (t.disliked IS NULL OR t.disliked = 0)"
        )
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT t.file_path, t.title, t.artist, t.bpm, t.starred, t.play_count, t.loudness_lufs "
                + join + " GROUP BY t.file_path", ids
            ).fetchall()
        return [dict(r) for r in rows]

    def get_listen_library(self) -> list[dict]:
        """Every playable library track for the Listen (regular, non-cadence)
        player's whole-library source: non-deleted, BPM or not. Shelf order
        (artist, album, disc, track) so "Play" reads like flipping through the
        collection; the client owns shuffle. Disliked tracks are included but
        flagged — playing the whole library is an explicit choice, unlike a
        run's auto-pick — and the radio refill filters them client-side."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT file_path, title, artist, bpm, starred, disliked, "
                "duration_ms, loudness_lufs FROM tracks WHERE status != 'deleted' "
                "ORDER BY artist COLLATE NOCASE, album COLLATE NOCASE, "
                "disc_no, track_no, file_path"
            ).fetchall()
        return [dict(r) for r in rows]

    def count_run_candidates(self, playlist_id: int) -> int:
        """How many of a playlist's tracks are actually runnable (matched + BPM)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT t.file_path) AS n " + self._PLAYLIST_RUN_JOIN,
                (playlist_id,),
            ).fetchone()
        return int(row["n"] or 0)

    def get_artist_tracks(self, name: str) -> list[dict]:
        """Every non-deleted track crediting this artist — via track_artists,
        so a featured/collab credit (e.g. "Argy, SOLANCE") links back to both
        artists, not just an exact match on the full combo string."""
        norm = normalize_artist_name(name)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT t.* FROM tracks t JOIN track_artists ta ON ta.track_id = t.id "
                "WHERE t.status != 'deleted' AND ta.norm_name = ? "
                "ORDER BY t.album, t.disc_no, t.track_no, t.file_path", (norm,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_tracks_under(self, dir_prefix: str) -> list[dict]:
        """(file_path, artist, album_artist) for every non-deleted track whose
        path starts with dir_prefix — used to check whether a folder belongs
        exclusively to one artist before writing an artist.jpg into it."""
        esc = dir_prefix.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT file_path, artist, album_artist FROM tracks "
                "WHERE status != 'deleted' AND file_path LIKE ? ESCAPE '!'",
                (esc + "%",)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_album_tracks(self, album: str, album_artist: Optional[str] = None) -> list[dict]:
        """Tracks on an album (optionally scoped to an album artist), disc/track ordered."""
        with self._connect() as conn:
            if album_artist:
                rows = conn.execute(
                    "SELECT * FROM tracks WHERE status != 'deleted' AND album = ? AND album_artist = ? "
                    "ORDER BY disc_no, track_no, file_path", (album, album_artist)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tracks WHERE status != 'deleted' AND album = ? "
                    "ORDER BY disc_no, track_no, file_path", (album,)).fetchall()
        return [dict(r) for r in rows]

    def list_artists(self) -> list[dict]:
        """Artist index: one row per individually credited artist (via
        track_artists), so a featured/collab credit like "Argy, SOLANCE" gives
        both artists their own browsable entry rather than one combined one."""
        with self._connect() as conn:
            rows = conn.execute("""
                WITH live AS (
                    SELECT ta.norm_name, ta.name, t.album, t.bpm, t.file_path
                    FROM track_artists ta JOIN tracks t ON t.id = ta.track_id
                    WHERE t.status != 'deleted'
                ),
                display AS (
                    SELECT norm_name, name FROM (
                        SELECT norm_name, name,
                               ROW_NUMBER() OVER (
                                   PARTITION BY norm_name
                                   ORDER BY COUNT(*) DESC, name COLLATE NOCASE
                               ) AS rn
                        FROM live GROUP BY norm_name, name
                    ) WHERE rn = 1
                )
                SELECT d.name AS name,
                       COUNT(*) AS tracks,
                       COUNT(DISTINCT NULLIF(l.album, '')) AS albums,
                       ROUND(AVG(NULLIF(l.bpm, 0)), 1) AS avg_bpm,
                       MIN(l.file_path) AS sample_path
                FROM live l JOIN display d ON d.norm_name = l.norm_name
                GROUP BY l.norm_name ORDER BY name COLLATE NOCASE
            """).fetchall()
        return [dict(r) for r in rows]

    def list_albums(self) -> list[dict]:
        """Album index: one row per album + album artist pair."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT album, COALESCE(album_artist, '') AS album_artist, "
                "COUNT(*) AS tracks, MAX(year) AS year, "
                "ROUND(AVG(NULLIF(bpm, 0)), 1) AS avg_bpm, "
                "MIN(file_path) AS sample_path "
                "FROM tracks WHERE status != 'deleted' "
                "AND COALESCE(album, '') != '' "
                "GROUP BY album, COALESCE(album_artist, '') "
                "ORDER BY album COLLATE NOCASE"
            ).fetchall()
        return [dict(r) for r in rows]

    def set_lyrics_state(self, file_path: str, status: str | None, synced: bool = False) -> None:
        """Record a track's lyrics state (embedded/fetched/not_found/instrumental)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE tracks SET lyrics_status = ?, lyrics_synced = ? WHERE file_path = ?",
                (status, int(synced), file_path))
            conn.commit()

    def get_tracks_missing_lyrics(self, limit: int = 2000,
                                  retry_not_found: bool = False) -> list[dict]:
        """Non-deleted tracks with no lyrics yet — the bulk-fill work list.
        Tracks without title+artist tags are skipped (nothing to look up)."""
        skip = ("embedded", "fetched", "instrumental") if retry_not_found else \
               ("embedded", "fetched", "instrumental", "not_found")
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT file_path, title, artist, album, duration_ms FROM tracks "
                f"WHERE status != 'deleted' "
                f"AND COALESCE(title, '') != '' AND COALESCE(artist, '') != '' "
                f"AND COALESCE(lyrics_status, '') NOT IN ({','.join('?' * len(skip))}) "
                f"ORDER BY analyzed_at DESC LIMIT ?", (*skip, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_tracks_missing_isrc(self, limit: int = 2000) -> list[dict]:
        """Non-deleted tracks with no ISRC yet — the bulk-fill work list."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT file_path, title, artist, duration_ms FROM tracks "
                "WHERE status != 'deleted' AND (isrc IS NULL OR isrc = '') "
                "ORDER BY analyzed_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_suspicious_count(self, conf_threshold: float, bpm_min: float, bpm_max: float) -> int:
        with self._connect() as conn:
            return conn.execute(
                f"SELECT COUNT(*) FROM tracks WHERE {self._SUSPICIOUS_WHERE}",
                (conf_threshold, bpm_min, bpm_max)
            ).fetchone()[0]

    def get_suspicious_page(self, conf_threshold: float, bpm_min: float, bpm_max: float,
                            limit: int, offset: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {self._SUSPICIOUS_COLS} FROM tracks WHERE {self._SUSPICIOUS_WHERE}"
                " ORDER BY bpm_confidence ASC NULLS LAST LIMIT ? OFFSET ?",
                (conf_threshold, bpm_min, bpm_max, limit, offset)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_suspicious(self, conf_threshold: float, bpm_min: float, bpm_max: float) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {self._SUSPICIOUS_COLS} FROM tracks WHERE {self._SUSPICIOUS_WHERE}"
                " ORDER BY bpm_confidence ASC NULLS LAST",
                (conf_threshold, bpm_min, bpm_max)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_reanalysis_queue(self) -> list[str]:
        """Return file paths of unlocked tracks that are candidates for re-analysis."""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT file_path FROM tracks
                WHERE locked = 0 AND status != 'deleted' AND (
                    needs_review = 1
                    OR status = 'error'
                    OR detector = 'librosa'
                )
                ORDER BY file_path
            """).fetchall()
            return [r[0] for r in rows]

    def mark_deleted(self, file_path: str) -> None:
        """Mark a single track as deleted (unlocked tracks only)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE tracks SET status = 'deleted' WHERE file_path = ? AND locked = 0",
                (file_path,),
            )
            conn.commit()

    def mark_deleted_bulk(self, file_paths: set) -> None:
        """Mark a set of tracks as deleted (unlocked tracks only)."""
        if not file_paths:
            return
        with self._connect() as conn:
            conn.executemany(
                "UPDATE tracks SET status = 'deleted' WHERE file_path = ? AND locked = 0",
                [(fp,) for fp in file_paths],
            )
            conn.commit()

    def count_deleted(self) -> int:
        """Number of tracks whose file is gone from the library (status='deleted')."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM tracks WHERE status = 'deleted'"
            ).fetchone()
        return row["n"]

    def purge_deleted(self) -> int:
        """Permanently drop every deleted-status row. Returns the number purged.

        Unrecoverable: these rows track files already removed from the library, so
        nothing on disk is touched — only the stale DB records are cleared."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM tracks WHERE status = 'deleted'")
            conn.commit()
            return cur.rowcount

    def bulk_register_pending(self, entries: list[tuple[str, str]], force: bool = False) -> None:
        """Insert/update entries as 'pending'. Locked tracks and (when !force)
        already-done unchanged tracks are left untouched."""
        if not entries:
            return
        if force:
            sql = """
                INSERT INTO tracks (file_path, file_hash, status)
                VALUES (?, ?, 'pending')
                ON CONFLICT(file_path) DO UPDATE SET
                    file_hash = excluded.file_hash,
                    status    = 'pending'
                WHERE locked = 0
            """
        else:
            sql = """
                INSERT INTO tracks (file_path, file_hash, status)
                VALUES (?, ?, 'pending')
                ON CONFLICT(file_path) DO UPDATE SET
                    file_hash = excluded.file_hash,
                    status    = 'pending'
                WHERE locked = 0 AND (status != 'done' OR file_hash != excluded.file_hash)
            """
        with self._connect() as conn:
            conn.executemany(sql, entries)
            conn.commit()

    def get_pending_tracks(self) -> list[str]:
        """Return file paths of unlocked tracks with status='pending'."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT file_path FROM tracks WHERE status = 'pending' AND locked = 0 ORDER BY file_path"
            ).fetchall()
        return [r["file_path"] for r in rows]

    def get_stats(self) -> dict:
        with self._connect() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(CASE WHEN status='done'    THEN 1 END) AS done,
                    COUNT(CASE WHEN status='error'   THEN 1 END) AS errors,
                    COUNT(CASE WHEN status='pending' THEN 1 END) AS pending,
                    COUNT(CASE WHEN status='deleted' THEN 1 END) AS deleted,
                    COUNT(CASE WHEN needs_review=1 AND status='done' AND locked=0 AND reviewed=0 THEN 1 END) AS needs_review,
                    COUNT(CASE WHEN reviewed=1       THEN 1 END) AS reviewed,
                    COUNT(CASE WHEN locked=1         THEN 1 END) AS locked,
                    COUNT(CASE WHEN (isrc IS NULL OR isrc='') AND status!='deleted' THEN 1 END) AS missing_isrc,
                    COUNT(CASE WHEN starred=1 AND status!='deleted' THEN 1 END) AS starred,
                    COUNT(CASE WHEN disliked=1 AND status!='deleted' THEN 1 END) AS disliked,
                    COUNT(CASE WHEN status!='deleted' AND NOT EXISTS (
                        SELECT 1 FROM playlist_tracks pt
                        WHERE pt.matched_file_path = tracks.file_path
                        AND pt.removed_at IS NULL) THEN 1 END) AS unplaylisted
                FROM tracks
            """).fetchone()
            return dict(row)

    def get_bpm_distribution(self, bucket: int = 5) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT CAST(bpm/? AS INT)*? AS b, COUNT(*) AS n "
                "FROM tracks WHERE status='done' AND bpm IS NOT NULL "
                "GROUP BY b ORDER BY b", (bucket, bucket)
            ).fetchall()
        return [{"bpm": r["b"], "count": r["n"]} for r in rows]

    def get_detector_distribution(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT COALESCE(detector,'unknown') AS d, COUNT(*) AS n "
                "FROM tracks WHERE status='done' GROUP BY d ORDER BY n DESC"
            ).fetchall()
        return [{"detector": r["d"], "count": r["n"]} for r in rows]

    def get_bpm_descriptive(self) -> dict:
        with self._connect() as conn:
            r = conn.execute(
                "SELECT ROUND(AVG(bpm),1) AS avg, MIN(bpm) AS mn, MAX(bpm) AS mx, "
                "COUNT(*) AS n FROM tracks WHERE status='done' AND bpm IS NOT NULL"
            ).fetchone()
            bpms = [row[0] for row in conn.execute(
                "SELECT bpm FROM tracks WHERE status='done' AND bpm IS NOT NULL ORDER BY bpm"
            ).fetchall()]
        if not bpms:
            return {"avg": None, "min": None, "max": None, "median": None, "count": 0}
        mid = len(bpms) // 2
        median = (bpms[mid - 1] + bpms[mid]) / 2 if len(bpms) % 2 == 0 else bpms[mid]
        return {"avg": r["avg"], "min": r["mn"], "max": r["mx"],
                "median": round(median, 1), "count": r["n"]}

    def get_source_stats(self) -> dict:
        """Where the library came from: grabber-managed vs pre-existing tracks,
        plus completed downloads per provider. For the Stats page (grabber on)."""
        with self._connect() as conn:
            src = conn.execute("""
                SELECT
                    COUNT(CASE WHEN managed=1 THEN 1 END) AS managed,
                    COUNT(CASE WHEN managed IS NULL OR managed=0 THEN 1 END) AS unmanaged
                FROM tracks WHERE status != 'deleted'
            """).fetchone()
            providers = conn.execute(
                "SELECT COALESCE(provider, 'unknown') AS p, COUNT(*) AS n "
                "FROM grab_queue WHERE status='done' GROUP BY p ORDER BY n DESC"
            ).fetchall()
        return {
            "managed": src["managed"],
            "unmanaged": src["unmanaged"],
            "providers": [{"provider": r["p"], "count": r["n"]} for r in providers],
        }

    # ══════════════════════════════════════════════════════════════════════════
    # Grabber (M3+)
    # ══════════════════════════════════════════════════════════════════════════

    # ── Tag indexing ──────────────────────────────────────────────────────────
    def clear_tag_index(self) -> int:
        """Forget which tracks have had their tags read, so the next index_tags()
        pass re-reads every file's metadata from disk. Used to pick up tag edits
        (e.g. newly-added ISRCs) made outside the app that didn't change mtime."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE tracks SET tags_indexed_hash = NULL WHERE status != 'deleted'")
            conn.commit()
            return cur.rowcount

    def get_tracks_needing_tag_index(self) -> list[dict]:
        """done/locked tracks whose tags haven't been read for the current hash."""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT file_path, file_hash FROM tracks
                WHERE status != 'deleted' AND (
                    tags_indexed_hash IS NULL OR tags_indexed_hash != file_hash
                )
            """).fetchall()
        return [dict(r) for r in rows]

    def _sync_track_artists(self, conn, file_path: str, artist: Optional[str],
                            album_artist: Optional[str]) -> None:
        """Rebuild this track's track_artists rows from its current artist/
        album_artist tags, so every credited artist (not just an exact match
        on the full multi-artist string) links back to the track."""
        row = conn.execute(
            "SELECT id FROM tracks WHERE file_path = ?", (file_path,)).fetchone()
        if not row:
            return
        track_id = row["id"]
        conn.execute("DELETE FROM track_artists WHERE track_id = ?", (track_id,))
        seen = set()
        for raw in (artist, album_artist):
            for name in split_artist_credits(raw):
                key = normalize_artist_name(name)
                if key and key not in seen:
                    seen.add(key)
                    conn.execute(
                        "INSERT OR IGNORE INTO track_artists (track_id, name, norm_name) "
                        "VALUES (?, ?, ?)", (track_id, name, key))

    def update_track_metadata(self, old_path: str, new_path: str, tags: dict,
                              file_hash: str) -> None:
        """Rewrite a track's descriptive tags and (if it moved) its file_path,
        stamping the post-write hash so the watcher won't re-analyze it."""
        with self._connect() as conn:
            conn.execute("""
                UPDATE tracks SET
                    file_path=?, file_hash=?, tags_indexed_hash=?,
                    title=?, artist=?, album=?, album_artist=?, track_no=?, disc_no=?,
                    year=?, isrc=?, norm_title=?, norm_artist=?
                WHERE file_path=?
            """, (new_path, file_hash, file_hash, tags.get("title"), tags.get("artist"),
                  tags.get("album"), tags.get("album_artist"), tags.get("track_no"),
                  tags.get("disc_no"), tags.get("year"), tags.get("isrc"),
                  tags.get("norm_title"), tags.get("norm_artist"), old_path))
            self._sync_track_artists(conn, new_path, tags.get("artist"), tags.get("album_artist"))
            conn.commit()

    def refresh_track_hash(self, file_path: str, file_hash: str) -> None:
        """Stamp the current file hash (after a cover/tag write) so the watcher skips it."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE tracks SET file_hash=?, tags_indexed_hash=? WHERE file_path=?",
                (file_hash, file_hash, file_path))
            conn.commit()

    def get_duplicates(self) -> list[dict]:
        """Groups of >1 non-deleted tracks that are likely the same recording.

        Two tracks are clustered when they share either a normalized artist+title
        OR the same ISRC. The ISRC edge catches duplicates whose tags/filenames
        differ (e.g. the same song as an .mp3 and an .m4a) as long as both carry
        the ISRC — which pure artist+title normalization misses.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT file_path, title, artist, album, bpm, managed, isrc, "
                "duration_ms, norm_artist, norm_title FROM tracks "
                "WHERE status != 'deleted'"
            ).fetchall()

        # Union-find over the two equivalence keys.
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            parent.setdefault(x, x)
            root = x
            while parent[root] != root:
                root = parent[root]
            while parent[x] != root:      # path compression
                parent[x], x = root, parent[x]
            return root

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        tracks: dict[str, dict] = {}
        by_norm: dict[tuple, str] = {}
        by_isrc: dict[str, str] = {}
        for r in rows:
            fp = r["file_path"]
            tracks[fp] = dict(r)
            find(fp)                       # register the node
            nt = (r["norm_title"] or "").strip()
            if nt:
                key = (r["norm_artist"], nt)
                if key in by_norm:
                    union(fp, by_norm[key])
                else:
                    by_norm[key] = fp
            isrc = (r["isrc"] or "").strip().upper()
            if isrc:
                if isrc in by_isrc:
                    union(fp, by_isrc[isrc])
                else:
                    by_isrc[isrc] = fp

        clusters: dict[str, list] = {}
        for fp in tracks:
            clusters.setdefault(find(fp), []).append(tracks[fp])

        dismissed = self.get_dismissed_signatures()
        out = []
        for members in clusters.values():
            if len(members) < 2:
                continue
            if _dupe_signature(m["file_path"] for m in members) in dismissed:
                continue                    # user marked this group "not a duplicate"
            first = members[0]
            out.append({
                "artist": first.get("norm_artist") or first.get("artist"),
                "title": first.get("norm_title") or first.get("title"),
                "count": len(members),
                "tracks": [{k: m.get(k) for k in
                            ("file_path", "title", "artist", "album", "bpm",
                             "managed", "isrc", "duration_ms")} for m in members],
            })
        out.sort(key=lambda g: (-g["count"], str(g["artist"] or ""), str(g["title"] or "")))
        return out

    def get_dismissed_signatures(self) -> set:
        with self._connect() as conn:
            return {r[0] for r in conn.execute("SELECT signature FROM dismissed_dupes").fetchall()}

    def dismiss_duplicate(self, paths: list) -> None:
        """Mark a set of tracks as 'not a duplicate' so the group stops showing."""
        sig = _dupe_signature(paths)
        if not sig:
            return
        with self._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO dismissed_dupes (signature) VALUES (?)", (sig,))
            conn.commit()

    def update_track_tags(self, file_path: str, tags: dict, file_hash: str) -> None:
        with self._connect() as conn:
            conn.execute("""
                UPDATE tracks SET
                    title=?, artist=?, album=?, album_artist=?, track_no=?, disc_no=?,
                    year=?, isrc=?, duration_ms=?, norm_title=?, norm_artist=?,
                    tags_indexed_hash=?
                WHERE file_path=?
            """, (tags.get("title"), tags.get("artist"), tags.get("album"),
                  tags.get("album_artist"), tags.get("track_no"), tags.get("disc_no"),
                  tags.get("year"), tags.get("isrc"), tags.get("duration_ms"),
                  tags.get("norm_title"), tags.get("norm_artist"), file_hash, file_path))
            self._sync_track_artists(conn, file_path, tags.get("artist"), tags.get("album_artist"))
            conn.commit()

    # ── Library matching support ──────────────────────────────────────────────
    def find_by_spotify_id(self, spotify_track_id: str) -> list[dict]:
        """Library tracks stamped with this Spotify track id (grabbed files carry
        it). An exact identity match, so 'do we already have it?' never depends on
        ISRC/fuzzy scoring or on the download-queue history surviving."""
        if not spotify_track_id:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tracks WHERE spotify_track_id = ? AND status != 'deleted'",
                (spotify_track_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def find_by_isrc(self, isrc: str) -> list[dict]:
        if not isrc:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tracks WHERE isrc = ? AND status != 'deleted'", (isrc,)
            ).fetchall()
        return [dict(r) for r in rows]

    def find_candidates_by_norm(self, norm_artist: str, norm_title: str) -> list[dict]:
        """Cheap SQL prefilter: rows sharing the normalized artist OR title.
        The fuzzy scorer in grabber.matching does the final decision."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tracks WHERE status != 'deleted' AND "
                "(norm_artist = ? OR norm_title = ?)",
                (norm_artist or "", norm_title or ""),
            ).fetchall()
        return [dict(r) for r in rows]
