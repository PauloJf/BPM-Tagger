"""SQLite database (WAL) for the track index, BPM results, and grabber state."""

import logging
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from .bpm.tags import get_file_hash
from .config import __version__

log = logging.getLogger(__name__)

# Grab-queue status state machine (§2). Non-terminal states mean the item is
# still "in flight"; exactly one non-terminal item may exist per spotify_track_id.
GRAB_TERMINAL = ("done", "failed", "skipped")
GRAB_NONTERMINAL = ("pending", "searching", "awaiting_user", "downloading",
                    "transcoding", "tagging", "analyzing_bpm")


class BPMDatabase:
    _SUSPICIOUS_WHERE = """status = 'done' AND locked = 0 AND reviewed = 0 AND (
        needs_review = 1
        OR (bpm_confidence IS NOT NULL AND bpm_confidence < ?)
        OR detector = 'librosa'
        OR (bpm IS NOT NULL AND (bpm < ? OR bpm > ?))
    )"""
    _SUSPICIOUS_COLS = (
        "file_path, bpm, bpm_dr, bpm_es, bpm_lb, bpm_confidence, detector, needs_review"
    )

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._backup_once()
        self._init_db()

    def _backup_once(self) -> None:
        """Copy the DB file to ``<db>.bak-<version>`` before migrating, once per
        version. No-op on a fresh/empty DB. Never fatal."""
        try:
            if not os.path.isfile(self.db_path) or os.path.getsize(self.db_path) == 0:
                return
            bak = f"{self.db_path}.bak-{__version__}"
            if os.path.exists(bak):
                return
            shutil.copy2(self.db_path, bak)
            log.info("DB backup written before migration: %s", os.path.basename(bak))
        except Exception as exc:  # pragma: no cover - best effort
            log.warning("DB pre-migration backup failed (continuing): %s", exc)

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tracks (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path      TEXT UNIQUE NOT NULL,
                    file_hash      TEXT,
                    bpm            REAL,
                    bpm_dr         REAL,
                    bpm_es         REAL,
                    bpm_lb         REAL,
                    bpm_confidence REAL,
                    detector       TEXT,
                    analyzed_at    TEXT,
                    status         TEXT DEFAULT 'pending',
                    error_message  TEXT,
                    needs_review   INTEGER DEFAULT 0,
                    reviewed       INTEGER DEFAULT 0,
                    locked         INTEGER DEFAULT 0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_path    ON tracks(file_path)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status  ON tracks(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_review  ON tracks(needs_review)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_locked  ON tracks(locked)")
            self._migrate(conn)
            conn.commit()

    def _migrate(self, conn):
        """Add columns/tables that may be absent in older databases. Additive only."""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(tracks)")}
        for col, coldef in [
            ("bpm_dr",         "REAL"),
            ("bpm_es",         "REAL"),
            ("bpm_lb",         "REAL"),
            ("needs_review",   "INTEGER DEFAULT 0"),
            ("reviewed",       "INTEGER DEFAULT 0"),
            ("locked",         "INTEGER DEFAULT 0"),
            ("waveform_peaks", "TEXT"),
            # ── Grabber tag index (M3) ────────────────────────────────────────
            ("title",          "TEXT"),
            ("artist",         "TEXT"),
            ("album",          "TEXT"),
            ("album_artist",   "TEXT"),
            ("track_no",       "INTEGER"),
            ("disc_no",        "INTEGER"),
            ("year",           "INTEGER"),
            ("isrc",           "TEXT"),
            ("duration_ms",    "INTEGER"),
            ("norm_title",     "TEXT"),
            ("norm_artist",    "TEXT"),
            ("managed",        "INTEGER DEFAULT 0"),
            ("spotify_track_id", "TEXT"),
            ("tags_indexed_hash", "TEXT"),  # file_hash at last tag-read pass
        ]:
            if col not in existing:
                conn.execute(f"ALTER TABLE tracks ADD COLUMN {col} {coldef}")
        # Pre-v1.0.4 lock_track() didn't clear needs_review; fix stale rows.
        conn.execute(
            "UPDATE tracks SET needs_review = 0 WHERE locked = 1 AND needs_review = 1"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_norm ON tracks(norm_artist, norm_title)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_isrc ON tracks(isrc)")

        self._create_grabber_tables(conn)

    def _create_grabber_tables(self, conn):
        """New grabber tables (§2). All CREATE ... IF NOT EXISTS — safe to re-run."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS playlists (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                spotify_id     TEXT UNIQUE NOT NULL,
                name           TEXT,
                snapshot_id    TEXT,
                enabled        INTEGER DEFAULT 1,
                image_url      TEXT,
                track_count    INTEGER DEFAULT 0,
                last_synced_at TEXT,
                created_at     TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS playlist_tracks (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_id      INTEGER NOT NULL,
                spotify_track_id TEXT,
                position         INTEGER,
                title            TEXT,
                artist           TEXT,
                album            TEXT,
                album_artist     TEXT,
                duration_ms      INTEGER,
                isrc             TEXT,
                track_no         INTEGER,
                disc_no          INTEGER,
                year             INTEGER,
                cover_url        TEXT,
                added_at         TEXT,
                norm_title       TEXT,
                norm_artist      TEXT,
                match_status     TEXT DEFAULT 'unknown',   -- have | missing | unknown
                matched_file_path TEXT,
                UNIQUE(playlist_id, position)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pt_playlist ON playlist_tracks(playlist_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pt_sid ON playlist_tracks(spotify_track_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS grab_queue (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_track_id  INTEGER,
                spotify_track_id   TEXT,
                title              TEXT,
                artist             TEXT,
                album              TEXT,
                album_artist       TEXT,
                duration_ms        INTEGER,
                isrc               TEXT,
                track_no           INTEGER,
                disc_no            INTEGER,
                year               INTEGER,
                cover_url          TEXT,
                status             TEXT DEFAULT 'pending',
                provider           TEXT,
                chosen_candidate_id INTEGER,
                search_override    TEXT,
                error              TEXT,
                attempts           INTEGER DEFAULT 0,
                progress           REAL DEFAULT 0,
                tmp_path           TEXT,
                final_path         TEXT,
                priority           INTEGER DEFAULT 0,
                created_at         TEXT,
                updated_at         TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gq_status ON grab_queue(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gq_sid ON grab_queue(spotify_track_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS grab_candidates (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                queue_item_id    INTEGER NOT NULL,
                provider         TEXT,
                provider_track_id TEXT,
                title            TEXT,
                artist           TEXT,
                album            TEXT,
                duration_ms      INTEGER,
                isrc             TEXT,
                quality          TEXT,
                score            REAL,
                score_breakdown  TEXT,
                url              TEXT,
                cover_url        TEXT,
                rank             INTEGER
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gc_item ON grab_candidates(queue_item_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS grab_events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                queue_item_id INTEGER NOT NULL,
                event         TEXT,
                detail        TEXT,
                created_at    TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ge_item ON grab_events(queue_item_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS oauth_tokens (
                service       TEXT UNIQUE NOT NULL,
                access_token  TEXT,
                refresh_token TEXT,
                expires_at    TEXT,
                scope         TEXT
            )
        """)

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
                     waveform_peaks: Optional[str] = None):
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO tracks
                    (file_path, file_hash, bpm, bpm_dr, bpm_es, bpm_lb, bpm_confidence,
                     detector, analyzed_at, status, needs_review, error_message, waveform_peaks)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    waveform_peaks = COALESCE(excluded.waveform_peaks, waveform_peaks)
            """, (file_path, file_hash, bpm, bpm_dr, bpm_es, bpm_lb, confidence,
                  detector, now, status, int(needs_review), error, waveform_peaks))
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

        updated = missing = 0
        with self._connect() as conn:
            for row in rows:
                fp = row["file_path"]
                if not os.path.exists(fp):
                    missing += 1
                    continue
                new_hash = get_file_hash(fp)
                if new_hash != row["file_hash"]:
                    conn.execute(
                        "UPDATE tracks SET file_hash = ? WHERE file_path = ?",
                        (new_hash, fp),
                    )
                    updated += 1
            conn.commit()
        return updated, missing

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

    def needs_analysis(self, file_path: str, file_hash: str) -> bool:
        track = self.get_track(file_path)
        if not track:
            return True
        if track["locked"]:
            return False
        if track["status"] != "done":
            return True
        return track["file_hash"] != file_hash

    def get_tracks_page(self, q: str, limit: int, offset: int,
                        filter: str = "",
                        bpm_target: Optional[float] = None,
                        bpm_tol: float = 5.0) -> tuple[list[dict], int]:
        if filter == "deleted":
            filter_clause = "status = 'deleted'"
            hide_deleted = False
        else:
            filter_clause = {
                "review": "needs_review = 1 AND locked = 0 AND reviewed = 0",
                "locked": "locked = 1",
            }.get(filter, "")
            hide_deleted = True

        with self._connect() as conn:
            params_count: list = []
            params_rows:  list = []
            clauses: list[str] = []

            if hide_deleted:
                clauses.append("status != 'deleted'")
            if q:
                clauses.append("file_path LIKE ?")
                params_count.append(f"%{q}%")
                params_rows.append(f"%{q}%")
            if filter_clause:
                clauses.append(filter_clause)
            if bpm_target is not None:
                clauses.append("bpm IS NOT NULL AND bpm BETWEEN ? AND ?")
                lo, hi = bpm_target - bpm_tol, bpm_target + bpm_tol
                params_count.extend([lo, hi])
                params_rows.extend([lo, hi])

            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            total = conn.execute(
                f"SELECT COUNT(*) FROM tracks {where}", params_count
            ).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM tracks {where} ORDER BY analyzed_at DESC LIMIT ? OFFSET ?",
                params_rows + [limit, offset]
            ).fetchall()
        return [dict(r) for r in rows], total

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
                    COUNT(CASE WHEN locked=1         THEN 1 END) AS locked
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

    # ══════════════════════════════════════════════════════════════════════════
    # Grabber (M3+)
    # ══════════════════════════════════════════════════════════════════════════

    # ── Tag indexing ──────────────────────────────────────────────────────────
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
            conn.commit()

    def refresh_track_hash(self, file_path: str, file_hash: str) -> None:
        """Stamp the current file hash (after a cover/tag write) so the watcher skips it."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE tracks SET file_hash=?, tags_indexed_hash=? WHERE file_path=?",
                (file_hash, file_hash, file_path))
            conn.commit()

    def get_duplicates(self) -> list[dict]:
        """Groups of >1 non-deleted tracks sharing normalized artist+title."""
        with self._connect() as conn:
            groups = conn.execute("""
                SELECT norm_artist, norm_title, COUNT(*) AS n
                FROM tracks
                WHERE status != 'deleted' AND norm_title IS NOT NULL AND norm_title != ''
                GROUP BY norm_artist, norm_title HAVING n > 1
                ORDER BY n DESC, norm_artist, norm_title
            """).fetchall()
            out = []
            for g in groups:
                rows = conn.execute(
                    "SELECT file_path, title, artist, album, bpm, managed FROM tracks "
                    "WHERE status != 'deleted' AND norm_artist IS ? AND norm_title = ?",
                    (g["norm_artist"], g["norm_title"])).fetchall()
                out.append({"artist": g["norm_artist"], "title": g["norm_title"],
                            "count": g["n"], "tracks": [dict(r) for r in rows]})
        return out

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
            conn.commit()

    # ── Library matching support ──────────────────────────────────────────────
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

    # ── OAuth tokens ──────────────────────────────────────────────────────────
    def get_oauth_token(self, service: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM oauth_tokens WHERE service = ?", (service,)
            ).fetchone()
        return dict(row) if row else None

    def save_oauth_token(self, service: str, access_token: str, refresh_token: str,
                         expires_at: str, scope: str) -> None:
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO oauth_tokens (service, access_token, refresh_token, expires_at, scope)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(service) DO UPDATE SET
                    access_token=excluded.access_token,
                    refresh_token=COALESCE(excluded.refresh_token, oauth_tokens.refresh_token),
                    expires_at=excluded.expires_at,
                    scope=excluded.scope
            """, (service, access_token, refresh_token, expires_at, scope))
            conn.commit()

    def delete_oauth_token(self, service: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM oauth_tokens WHERE service = ?", (service,))
            conn.commit()

    # ── Playlists ─────────────────────────────────────────────────────────────
    def add_playlist(self, spotify_id: str, name: str, image_url: str = "",
                     track_count: int = 0) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cur = conn.execute("""
                INSERT INTO playlists (spotify_id, name, image_url, track_count, enabled, created_at)
                VALUES (?, ?, ?, ?, 1, ?)
                ON CONFLICT(spotify_id) DO UPDATE SET
                    name=excluded.name, image_url=excluded.image_url,
                    track_count=excluded.track_count, enabled=1
            """, (spotify_id, name, image_url, track_count, now))
            conn.commit()
            row = conn.execute("SELECT id FROM playlists WHERE spotify_id = ?", (spotify_id,)).fetchone()
            return row["id"] if row else cur.lastrowid

    def list_playlists(self) -> list[dict]:
        with self._connect() as conn:
            playlists = [dict(r) for r in conn.execute(
                "SELECT * FROM playlists ORDER BY name COLLATE NOCASE"
            ).fetchall()]
            queued = {r["spotify_track_id"] for r in conn.execute(
                f"SELECT DISTINCT spotify_track_id FROM grab_queue "
                f"WHERE status IN ({','.join('?' * len(GRAB_NONTERMINAL))})",
                GRAB_NONTERMINAL,
            ).fetchall()}
            for p in playlists:
                rows = conn.execute(
                    "SELECT spotify_track_id, match_status FROM playlist_tracks WHERE playlist_id = ?",
                    (p["id"],),
                ).fetchall()
                have = miss = q = 0
                for r in rows:
                    if r["spotify_track_id"] in queued:
                        q += 1
                    elif r["match_status"] == "have":
                        have += 1
                    else:
                        miss += 1
                p["have_count"] = have
                p["missing_count"] = miss
                p["queued_count"] = q
                p["indexed_count"] = len(rows)
        return playlists

    def get_playlist(self, playlist_id: int) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
        return dict(row) if row else None

    def get_playlist_by_spotify_id(self, spotify_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM playlists WHERE spotify_id = ?", (spotify_id,)).fetchone()
        return dict(row) if row else None

    def get_enabled_playlists(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM playlists WHERE enabled = 1").fetchall()
        return [dict(r) for r in rows]

    def set_playlist_enabled(self, playlist_id: int, enabled: bool) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE playlists SET enabled = ? WHERE id = ?",
                         (int(enabled), playlist_id))
            conn.commit()

    def delete_playlist(self, playlist_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,))
            conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
            conn.commit()

    def update_playlist_sync(self, playlist_id: int, snapshot_id: str, name: str,
                             image_url: str, track_count: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("""
                UPDATE playlists SET snapshot_id=?, name=?, image_url=?, track_count=?,
                    last_synced_at=? WHERE id=?
            """, (snapshot_id, name, image_url, track_count, now, playlist_id))
            conn.commit()

    # ── Playlist tracks ───────────────────────────────────────────────────────
    def replace_playlist_tracks(self, playlist_id: int, tracks: list[dict]) -> None:
        """Rebuild a playlist's track rows in one transaction (per snapshot change)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,))
            conn.executemany("""
                INSERT INTO playlist_tracks
                    (playlist_id, spotify_track_id, position, title, artist, album,
                     album_artist, duration_ms, isrc, track_no, disc_no, year, cover_url,
                     added_at, norm_title, norm_artist, match_status, matched_file_path)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, [(playlist_id, t.get("spotify_track_id"), t.get("position"), t.get("title"),
                   t.get("artist"), t.get("album"), t.get("album_artist"), t.get("duration_ms"),
                   t.get("isrc"), t.get("track_no"), t.get("disc_no"), t.get("year"),
                   t.get("cover_url"), t.get("added_at"), t.get("norm_title"),
                   t.get("norm_artist"), t.get("match_status", "unknown"),
                   t.get("matched_file_path")) for t in tracks])
            conn.commit()

    def get_playlist_track_rows(self, playlist_id: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
                (playlist_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def set_playlist_track_match(self, pt_id: int, match_status: str,
                                 matched_file_path: Optional[str]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE playlist_tracks SET match_status=?, matched_file_path=? WHERE id=?",
                (match_status, matched_file_path, pt_id),
            )
            conn.commit()

    def _queued_sids(self, conn) -> set:
        return {r["spotify_track_id"] for r in conn.execute(
            f"SELECT DISTINCT spotify_track_id FROM grab_queue "
            f"WHERE status IN ({','.join('?' * len(GRAB_NONTERMINAL))})",
            GRAB_NONTERMINAL,
        ).fetchall()}

    def get_playlist_tracks(self, playlist_id: int, status: str = "") -> list[dict]:
        """Playlist tracks with a derived per-row status (have|queued|missing)."""
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
                (playlist_id,),
            ).fetchall()]
            queued = self._queued_sids(conn)
        for r in rows:
            if r["spotify_track_id"] in queued:
                r["derived_status"] = "queued"
            elif r["match_status"] == "have":
                r["derived_status"] = "have"
            else:
                r["derived_status"] = "missing"
        if status:
            rows = [r for r in rows if r["derived_status"] == status]
        return rows

    # ── Grab queue ────────────────────────────────────────────────────────────
    def has_any_grab(self, spotify_track_id: str) -> bool:
        """Any queue row (terminal or not) exists for this track. Used by the sync
        auto-enqueue so failed/skipped items aren't retried on every cycle."""
        if not spotify_track_id:
            return False
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM grab_queue WHERE spotify_track_id = ? LIMIT 1",
                (spotify_track_id,)).fetchone()
        return row is not None

    def has_nonterminal_grab(self, spotify_track_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT 1 FROM grab_queue WHERE spotify_track_id = ? "
                f"AND status IN ({','.join('?' * len(GRAB_NONTERMINAL))}) LIMIT 1",
                (spotify_track_id, *GRAB_NONTERMINAL),
            ).fetchone()
        return row is not None

    def enqueue_grab(self, meta: dict) -> Optional[int]:
        """Insert a pending grab_queue item unless a non-terminal one already
        exists for this spotify_track_id. Returns the new id, or None if skipped."""
        sid = meta.get("spotify_track_id")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            if sid:
                exists = conn.execute(
                    f"SELECT 1 FROM grab_queue WHERE spotify_track_id = ? "
                    f"AND status IN ({','.join('?' * len(GRAB_NONTERMINAL))}) LIMIT 1",
                    (sid, *GRAB_NONTERMINAL),
                ).fetchone()
                if exists:
                    return None
            cur = conn.execute("""
                INSERT INTO grab_queue
                    (playlist_track_id, spotify_track_id, title, artist, album, album_artist,
                     duration_ms, isrc, track_no, disc_no, year, cover_url, status,
                     priority, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'pending', ?, ?, ?)
            """, (meta.get("playlist_track_id"), sid, meta.get("title"), meta.get("artist"),
                  meta.get("album"), meta.get("album_artist"), meta.get("duration_ms"),
                  meta.get("isrc"), meta.get("track_no"), meta.get("disc_no"),
                  meta.get("year"), meta.get("cover_url"), meta.get("priority", 0), now, now))
            item_id = cur.lastrowid
            conn.execute(
                "INSERT INTO grab_events (queue_item_id, event, detail, created_at) VALUES (?,?,?,?)",
                (item_id, "enqueued", meta.get("title", ""), now),
            )
            conn.commit()
            return item_id

    def transition(self, item_id: int, status: str, detail: str = "") -> None:
        """Move a queue item to a new status and append an audit event."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("UPDATE grab_queue SET status=?, updated_at=? WHERE id=?",
                         (status, now, item_id))
            conn.execute(
                "INSERT INTO grab_events (queue_item_id, event, detail, created_at) VALUES (?,?,?,?)",
                (item_id, status, detail, now),
            )
            conn.commit()

    def get_queue_counts(self) -> dict:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM grab_queue GROUP BY status"
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def claim_next_grab(self) -> Optional[dict]:
        """Atomically move the highest-priority pending item to 'searching' and
        return it. CAS on status so concurrent workers can't claim the same row."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM grab_queue WHERE status='pending' "
                "ORDER BY priority DESC, id ASC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            cur = conn.execute(
                "UPDATE grab_queue SET status='searching', updated_at=? WHERE id=? AND status='pending'",
                (now, row["id"]),
            )
            conn.commit()
            if cur.rowcount != 1:
                return None
            item = dict(row)
            item["status"] = "searching"
            return item

    def update_grab(self, item_id: int, **fields) -> None:
        if not fields:
            return
        fields["updated_at"] = datetime.now(timezone.utc).isoformat()
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._connect() as conn:
            conn.execute(f"UPDATE grab_queue SET {cols} WHERE id=?",
                         (*fields.values(), item_id))
            conn.commit()

    def get_grab_item(self, item_id: int) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM grab_queue WHERE id=?", (item_id,)).fetchone()
        return dict(row) if row else None

    def get_queue(self, status: str = "") -> list[dict]:
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM grab_queue WHERE status=? ORDER BY priority DESC, id DESC",
                    (status,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM grab_queue ORDER BY priority DESC, id DESC").fetchall()
        return [dict(r) for r in rows]

    def get_last_change(self) -> str:
        """Cheap token that changes whenever grabber state does — lets the client
        gate cache invalidation instead of blindly refetching lists."""
        with self._connect() as conn:
            q = conn.execute("SELECT MAX(updated_at) FROM grab_queue").fetchone()[0] or ""
            p = conn.execute("SELECT MAX(last_synced_at) FROM playlists").fetchone()[0] or ""
            n = conn.execute("SELECT COUNT(*) FROM grab_queue").fetchone()[0]
        return f"{q}|{p}|{n}"

    def get_active_grabs(self) -> list[dict]:
        """Non-terminal queue items (for the live status poll)."""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT id, status, progress, title, artist FROM grab_queue "
                f"WHERE status IN ({','.join('?' * len(GRAB_NONTERMINAL))}) "
                f"ORDER BY priority DESC, id ASC", GRAB_NONTERMINAL).fetchall()
        return [dict(r) for r in rows]

    def get_grab_history(self, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM grab_queue WHERE status IN "
                f"({','.join('?' * len(GRAB_TERMINAL))}) ORDER BY updated_at DESC LIMIT ?",
                (*GRAB_TERMINAL, limit)).fetchall()
        return [dict(r) for r in rows]

    def add_grab_candidates(self, item_id: int, candidates: list[dict]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM grab_candidates WHERE queue_item_id=?", (item_id,))
            conn.executemany("""
                INSERT INTO grab_candidates
                    (queue_item_id, provider, provider_track_id, title, artist, album,
                     duration_ms, isrc, quality, score, score_breakdown, url, cover_url, rank)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, [(item_id, c.get("provider"), c.get("provider_track_id"), c.get("title"),
                   c.get("artist"), c.get("album"), c.get("duration_ms"), c.get("isrc"),
                   c.get("quality"), c.get("score"), c.get("score_breakdown"),
                   c.get("url"), c.get("cover_url"), c.get("rank")) for c in candidates])
            conn.commit()

    def get_grab_candidates(self, item_id: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM grab_candidates WHERE queue_item_id=? ORDER BY rank",
                (item_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_grab_candidate(self, candidate_id: int) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM grab_candidates WHERE id=?",
                               (candidate_id,)).fetchone()
        return dict(row) if row else None

    def get_grab_events(self, item_id: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM grab_events WHERE queue_item_id=? ORDER BY id", (item_id,)).fetchall()
        return [dict(r) for r in rows]

    def reset_inflight_grabs(self) -> int:
        """Startup recovery: return in-flight (non-terminal, non-awaiting) items to
        'pending'. Leaves awaiting_user (needs the human) alone."""
        inflight = ("searching", "downloading", "transcoding", "tagging", "analyzing_bpm")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE grab_queue SET status='pending', progress=0, updated_at=? "
                f"WHERE status IN ({','.join('?' * len(inflight))})",
                (now, *inflight))
            conn.commit()
            return cur.rowcount

    def record_managed_track(self, file_path: str, file_hash: str, meta: dict,
                             bpm: Optional[float], bpm_dr, bpm_es, bpm_lb,
                             confidence, detector: str, spotify_track_id: str) -> None:
        """Insert/replace a downloaded, tagged, BPM-analyzed track as managed=1.
        file_hash MUST be computed AFTER the BPM tag write (watcher anti-loop)."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO tracks
                    (file_path, file_hash, bpm, bpm_dr, bpm_es, bpm_lb, bpm_confidence,
                     detector, analyzed_at, status, needs_review, managed, spotify_track_id,
                     title, artist, album, album_artist, track_no, disc_no, year, isrc,
                     duration_ms, norm_title, norm_artist, tags_indexed_hash)
                VALUES (?,?,?,?,?,?,?,?,?, 'done', 0, 1, ?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(file_path) DO UPDATE SET
                    file_hash=excluded.file_hash, bpm=excluded.bpm, bpm_dr=excluded.bpm_dr,
                    bpm_es=excluded.bpm_es, bpm_lb=excluded.bpm_lb,
                    bpm_confidence=excluded.bpm_confidence, detector=excluded.detector,
                    analyzed_at=excluded.analyzed_at, status='done', managed=1,
                    spotify_track_id=excluded.spotify_track_id, title=excluded.title,
                    artist=excluded.artist, album=excluded.album,
                    album_artist=excluded.album_artist, track_no=excluded.track_no,
                    disc_no=excluded.disc_no, year=excluded.year, isrc=excluded.isrc,
                    duration_ms=excluded.duration_ms, norm_title=excluded.norm_title,
                    norm_artist=excluded.norm_artist, tags_indexed_hash=excluded.tags_indexed_hash
            """, (file_path, file_hash, bpm, bpm_dr, bpm_es, bpm_lb, confidence, detector, now,
                  spotify_track_id, meta.get("title"), meta.get("artist"), meta.get("album"),
                  meta.get("album_artist"), meta.get("track_no"), meta.get("disc_no"),
                  meta.get("year"), meta.get("isrc"), meta.get("duration_ms"),
                  meta.get("norm_title"), meta.get("norm_artist"), file_hash))
            conn.commit()
