"""SQLite database (WAL) for the track index and BPM results."""

import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from .bpm.tags import get_file_hash

log = logging.getLogger(__name__)


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
        self._init_db()

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
        """Add columns that may be absent in databases created before this version."""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(tracks)")}
        for col, coldef in [
            ("bpm_dr",         "REAL"),
            ("bpm_es",         "REAL"),
            ("bpm_lb",         "REAL"),
            ("needs_review",   "INTEGER DEFAULT 0"),
            ("reviewed",       "INTEGER DEFAULT 0"),
            ("locked",         "INTEGER DEFAULT 0"),
            ("waveform_peaks", "TEXT"),
        ]:
            if col not in existing:
                conn.execute(f"ALTER TABLE tracks ADD COLUMN {col} {coldef}")
        # Pre-v1.0.4 lock_track() didn't clear needs_review; fix stale rows.
        conn.execute(
            "UPDATE tracks SET needs_review = 0 WHERE locked = 1 AND needs_review = 1"
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
