"""Grab queue, candidates, audit events, OAuth tokens, managed tracks."""

from datetime import datetime, timezone
from typing import Optional

from .constants import GRAB_NONTERMINAL, GRAB_TERMINAL

class GrabberMixin:
    # Audit events kept per queue item. grab_queue rows persist indefinitely, so
    # the per-item log is capped to keep it from growing without bound.
    _EVENTS_PER_ITEM_CAP = 100

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

    _GRAB_INSERT_COLS = ("playlist_track_id, spotify_track_id, title, artist, album, "
                         "album_artist, duration_ms, isrc, track_no, disc_no, year, "
                         "cover_url, status, priority, created_at, updated_at")

    def enqueue_grab(self, meta: dict) -> Optional[int]:
        """Insert a pending grab_queue item unless a non-terminal one already exists
        for this track. Returns the new id, or None if skipped.

        Dedupe key: ``spotify_track_id`` when present (atomic
        ``INSERT ... SELECT ... WHERE NOT EXISTS`` so concurrent callers can't both
        pass a check-then-insert). For tracks with NO spotify id (a Navidrome
        playlist's missing track, a Deezer suggestion), fall back to a normalized
        ``(title, artist)`` guard against the non-terminal queue — so every caller is
        deduped, not just the Spotify ones. Both dedupes are non-terminal-only: a
        re-grab after a terminal state (done/failed/skipped) is always allowed."""
        sid = meta.get("spotify_track_id")
        now = datetime.now(timezone.utc).isoformat()
        vals = (meta.get("playlist_track_id"), sid, meta.get("title"), meta.get("artist"),
                meta.get("album"), meta.get("album_artist"), meta.get("duration_ms"),
                meta.get("isrc"), meta.get("track_no"), meta.get("disc_no"),
                meta.get("year"), meta.get("cover_url"), "pending",
                meta.get("priority", 0), now, now)
        with self._connect() as conn:
            if sid:
                placeholders = ",".join("?" * len(GRAB_NONTERMINAL))
                cur = conn.execute(
                    f"INSERT INTO grab_queue ({self._GRAB_INSERT_COLS}) "
                    f"SELECT ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,? "
                    f"WHERE NOT EXISTS (SELECT 1 FROM grab_queue "
                    f"WHERE spotify_track_id = ? AND status IN ({placeholders}))",
                    (*vals, sid, *GRAB_NONTERMINAL),
                )
                if cur.rowcount == 0:
                    return None
            else:
                from ..grabber.matching import normalize_artist, normalize_title
                key = (normalize_title(meta.get("title")), normalize_artist(meta.get("artist")))
                if key != ("", ""):
                    placeholders = ",".join("?" * len(GRAB_NONTERMINAL))
                    for r in conn.execute(
                            f"SELECT title, artist FROM grab_queue "
                            f"WHERE status IN ({placeholders})", GRAB_NONTERMINAL):
                        if (normalize_title(r["title"]), normalize_artist(r["artist"])) == key:
                            return None
                cur = conn.execute(
                    f"INSERT INTO grab_queue ({self._GRAB_INSERT_COLS}) "
                    f"VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", vals)
            item_id = cur.lastrowid
            self._append_event(conn, item_id, "enqueued", meta.get("title", ""), now)
            conn.commit()
            return item_id

    def _append_event(self, conn, item_id: int, event: str, detail: str, now: str) -> None:
        """Insert one audit event and trim the item's history to the most recent
        ``_EVENTS_PER_ITEM_CAP`` rows. The caller owns the transaction (commits)."""
        conn.execute(
            "INSERT INTO grab_events (queue_item_id, event, detail, created_at) VALUES (?,?,?,?)",
            (item_id, event, detail, now),
        )
        conn.execute(
            "DELETE FROM grab_events WHERE queue_item_id=? AND id NOT IN "
            "(SELECT id FROM grab_events WHERE queue_item_id=? ORDER BY id DESC LIMIT ?)",
            (item_id, item_id, self._EVENTS_PER_ITEM_CAP),
        )

    def transition(self, item_id: int, status: str, detail: str = "") -> None:
        """Move a queue item to a new status and append an audit event."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("UPDATE grab_queue SET status=?, updated_at=? WHERE id=?",
                         (status, now, item_id))
            self._append_event(conn, item_id, status, detail, now)
            conn.commit()

    def add_grab_event(self, item_id: int, event: str, detail: str = "") -> None:
        """Append an audit event to a queue item without changing its status
        (e.g. a non-fatal tag/cover warning during an otherwise-successful grab)."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            self._append_event(conn, item_id, event, detail, now)
            conn.commit()

    def prune_grab_events(self) -> int:
        """Enforce the per-item event cap across every item — one-time cleanup for
        databases that accumulated unbounded history before the cap existed.
        Returns the number of rows removed."""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM grab_events WHERE id NOT IN ("
                "  SELECT id FROM ("
                "    SELECT id, ROW_NUMBER() OVER "
                "      (PARTITION BY queue_item_id ORDER BY id DESC) AS rn"
                "    FROM grab_events) WHERE rn <= ?)",
                (self._EVENTS_PER_ITEM_CAP,),
            )
            conn.commit()
            return cur.rowcount

    def bump_grabbed_total(self, n: int = 1) -> None:
        """Increment the all-time 'tracks grabbed' counter. Persistent and
        independent of the queue rows, so it survives Clear completed / Delete."""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO app_counters (key, value) VALUES ('grabbed_total', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = value + excluded.value",
                (n,))
            conn.commit()

    def get_grabbed_total(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM app_counters WHERE key='grabbed_total'").fetchone()
        return int(row["value"]) if row else 0

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

    def delete_grab(self, item_id: int) -> bool:
        """Remove a queue item and its candidates/events. Deletes the children
        explicitly (not just via ON DELETE CASCADE) so it's correct on older
        databases created before FK enforcement. Returns True if a row was
        removed. Note: this only drops the queue bookkeeping — a file already
        downloaded and filed into the library is untouched."""
        with self._connect() as conn:
            conn.execute("DELETE FROM grab_candidates WHERE queue_item_id=?", (item_id,))
            conn.execute("DELETE FROM grab_events WHERE queue_item_id=?", (item_id,))
            cur = conn.execute("DELETE FROM grab_queue WHERE id=?", (item_id,))
            conn.commit()
            return cur.rowcount > 0

    def delete_completed_grabs(self) -> int:
        """Remove every completed ('done') queue item and its children — the
        History "Clear completed" action. Downloaded files already filed into the
        library are untouched; this only clears the queue bookkeeping. Returns the
        number of items removed."""
        with self._connect() as conn:
            ids = [r[0] for r in conn.execute(
                "SELECT id FROM grab_queue WHERE status='done'").fetchall()]
            if not ids:
                return 0
            qs = ",".join("?" * len(ids))
            conn.execute(f"DELETE FROM grab_candidates WHERE queue_item_id IN ({qs})", ids)
            conn.execute(f"DELETE FROM grab_events WHERE queue_item_id IN ({qs})", ids)
            cur = conn.execute(f"DELETE FROM grab_queue WHERE id IN ({qs})", ids)
            conn.commit()
            return cur.rowcount

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
                             confidence, detector: str, spotify_track_id: str,
                             loudness_lufs: Optional[float] = None,
                             loudness_source: Optional[str] = None) -> None:
        """Insert/replace a downloaded, tagged, BPM-analyzed track as managed=1.
        file_hash MUST be computed AFTER the BPM tag write (watcher anti-loop)."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO tracks
                    (file_path, file_hash, bpm, bpm_dr, bpm_es, bpm_lb, bpm_confidence,
                     detector, analyzed_at, status, needs_review, managed, spotify_track_id,
                     title, artist, album, album_artist, track_no, disc_no, year, isrc,
                     duration_ms, norm_title, norm_artist, tags_indexed_hash,
                     loudness_lufs, loudness_source)
                VALUES (?,?,?,?,?,?,?,?,?, 'done', 0, 1, ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                    norm_artist=excluded.norm_artist, tags_indexed_hash=excluded.tags_indexed_hash,
                    loudness_lufs=COALESCE(excluded.loudness_lufs, loudness_lufs),
                    loudness_source=COALESCE(excluded.loudness_source, loudness_source)
            """, (file_path, file_hash, bpm, bpm_dr, bpm_es, bpm_lb, confidence, detector, now,
                  spotify_track_id, meta.get("title"), meta.get("artist"), meta.get("album"),
                  meta.get("album_artist"), meta.get("track_no"), meta.get("disc_no"),
                  meta.get("year"), meta.get("isrc"), meta.get("duration_ms"),
                  meta.get("norm_title"), meta.get("norm_artist"), file_hash,
                  loudness_lufs, loudness_source))
            conn.commit()
