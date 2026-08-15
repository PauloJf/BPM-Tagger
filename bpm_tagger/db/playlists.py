"""Playlists and playlist-track membership (Spotify + Navidrome)."""

from datetime import datetime, timezone
from typing import Optional

from .constants import GRAB_NONTERMINAL

class PlaylistsMixin:
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

    def add_navidrome_playlist(self, navidrome_id: str, name: str, image_url: str = "",
                               track_count: int = 0) -> int:
        """Register (or refresh) a Navidrome-sourced playlist. Keyed on navidrome_id."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO playlists (source, navidrome_id, name, image_url, track_count, enabled, created_at)
                VALUES ('navidrome', ?, ?, ?, ?, 1, ?)
                ON CONFLICT(navidrome_id) DO UPDATE SET
                    name=excluded.name, image_url=excluded.image_url,
                    track_count=excluded.track_count, enabled=1
            """, (navidrome_id, name, image_url, track_count, now))
            conn.commit()
            row = conn.execute("SELECT id FROM playlists WHERE navidrome_id = ?",
                               (navidrome_id,)).fetchone()
            return row["id"] if row else None

    def add_local_playlist(self, name: str, image_url: str = "") -> int:
        """Create an empty Local playlist (source='local', no external id). Tracks are
        added by the user via add_track_to_local_playlist(); Local playlists never sync,
        so membership changes there are explicit adds/removes, not diff tombstones."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO playlists (source, name, image_url, track_count, enabled, created_at) "
                "VALUES ('local', ?, ?, 0, 1, ?)", (name, image_url, now))
            conn.commit()
            return cur.lastrowid

    def mark_playlist_synced(self, playlist_id: int, name: Optional[str] = None,
                             image_url: Optional[str] = None,
                             track_count: Optional[int] = None) -> None:
        """Stamp last_synced_at (and optionally refresh meta) — the source-agnostic
        counterpart to update_playlist_sync (which also carries a Spotify snapshot)."""
        now = datetime.now(timezone.utc).isoformat()
        sets, vals = ["last_synced_at = ?"], [now]
        if name is not None:
            sets.append("name = ?"); vals.append(name)
        if image_url is not None:
            sets.append("image_url = ?"); vals.append(image_url)
        if track_count is not None:
            sets.append("track_count = ?"); vals.append(track_count)
        vals.append(playlist_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE playlists SET {', '.join(sets)} WHERE id = ?", vals)
            conn.commit()

    def update_playlist_meta(self, playlist_id: int, *, name: Optional[str] = None,
                             description: Optional[str] = None,
                             pinned: Optional[bool] = None) -> None:
        """Update the user-authored playlist fields, skipping the ones left None.

        Only `name` is owned by a synced source (mark_playlist_synced /
        update_playlist_sync overwrite it on every sync), so policing *which*
        sources may be renamed is the API's job — this method just writes what
        it's given. `description` and `pinned` are never touched by sync, so they
        persist on Spotify and Navidrome mirrors too."""
        sets, vals = [], []
        if name is not None:
            sets.append("name = ?"); vals.append(name)
        if description is not None:
            sets.append("description = ?"); vals.append(description)
        if pinned is not None:
            sets.append("pinned = ?"); vals.append(int(pinned))
        if not sets:
            return
        vals.append(playlist_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE playlists SET {', '.join(sets)} WHERE id = ?", vals)
            conn.commit()

    def _attach_counts(self, conn, p: dict, queued: set, queued_pt: set) -> None:
        """Fold a playlist's live rows into coverage counts (have / missing / queued /
        new / removed / indexed) on the dict in place. Tombstones (removed_at set) are
        out of the coverage totals. `queued` / `queued_pt` are the in-flight-grab sets,
        computed once by the caller so a bulk listing needn't recompute them per row."""
        rows = conn.execute(
            "SELECT id, spotify_track_id, match_status, is_new, removed_at "
            "FROM playlist_tracks WHERE playlist_id = ?",
            (p["id"],),
        ).fetchall()
        have = miss = q = new = removed = 0
        for r in rows:
            if r["removed_at"]:            # tombstone: out of coverage totals
                removed += 1
                continue
            if r["is_new"]:
                new += 1
            if r["spotify_track_id"] in queued or r["id"] in queued_pt:
                q += 1
            elif r["match_status"] == "have":
                have += 1
            else:
                miss += 1
        p["have_count"] = have
        p["missing_count"] = miss
        p["queued_count"] = q
        p["new_count"] = new
        p["removed_count"] = removed
        p["indexed_count"] = have + miss + q   # live tracks only

    def list_playlists(self) -> list[dict]:
        with self._connect() as conn:
            # Pinned first, then alphabetical — the "custom ordering" of the
            # playlist list. Everything reading this inherits it, including the
            # Run page's source picker (/api/run/playlists).
            playlists = [dict(r) for r in conn.execute(
                "SELECT * FROM playlists ORDER BY pinned DESC, name COLLATE NOCASE"
            ).fetchall()]
            queued = self._queued_sids(conn)
            queued_pt = self._queued_pt_ids(conn)
            for p in playlists:
                self._attach_counts(conn, p, queued, queued_pt)
        return playlists

    def playlist_names(self) -> dict:
        """{id: name} — the cheap lookup for labelling a reference to a playlist
        (e.g. a run journal row's source) without paying for list_playlists()'
        per-playlist coverage counts."""
        with self._connect() as conn:
            rows = conn.execute("SELECT id, name FROM playlists").fetchall()
        return {r["id"]: r["name"] for r in rows}

    def get_playlist(self, playlist_id: int, with_counts: bool = False) -> Optional[dict]:
        """One playlist row. With `with_counts`, also fold in the same coverage counts
        list_playlists() attaches — the detail view needs them for its chips, tab
        list, and the have-tracks-dependent header actions (Export / Add-all)."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
            if row is None:
                return None
            p = dict(row)
            if with_counts:
                self._attach_counts(conn, p, self._queued_sids(conn), self._queued_pt_ids(conn))
        return p

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
            # player_playlists carries no FK constraint (the playlists table can be
            # rebuilt by the legacy migration), so drop the player-scope join rows for
            # this playlist here — a deleted playlist can't linger in any user's
            # allowlist (Phase 5).
            conn.execute("DELETE FROM player_playlists WHERE playlist_id = ?", (playlist_id,))
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
    _PT_META_COLS = ("position", "title", "artist", "album", "album_artist",
                     "duration_ms", "isrc", "track_no", "disc_no", "year", "cover_url",
                     "added_at", "norm_title", "norm_artist", "spotify_track_id")

    def sync_playlist_tracks(self, playlist_id: int, tracks: list[dict]) -> tuple[int, int]:
        """Diff incoming source tracks against stored rows, keyed by source_track_id.

        Unlike a wipe-and-reinsert, this preserves rows (and their ids, match state,
        and dates) across syncs so membership changes are observable:

        * present in source, not stored          → insert, is_new=1, first_seen_at=now
        * present in both                         → update metadata/position; revive a
                                                     tombstone (removed_at→NULL, is_new=1)
        * stored, absent from source, live        → tombstone (removed_at=now)
        * stored, absent from source, already a
          tombstone from a prior sync             → delete (the tombstone has been shown)

        So a detail view always reflects "changes since the last sync". is_new is left
        for mark_playlist_seen() to clear on view. Returns (added, removed) counts.
        Duplicate source ids within one playlist collapse to a single row.
        """
        now = datetime.now(timezone.utc).isoformat()

        def _sid(t: dict):
            return t.get("source_track_id") or t.get("spotify_track_id")

        with self._connect() as conn:
            existing: dict = {}
            for r in conn.execute(
                    "SELECT * FROM playlist_tracks WHERE playlist_id = ? ORDER BY id",
                    (playlist_id,)):
                sid = r["source_track_id"]
                if sid in existing:
                    conn.execute("DELETE FROM playlist_tracks WHERE id = ?", (r["id"],))
                else:
                    existing[sid] = r

            incoming_ids, added = set(), 0
            for t in tracks:
                sid = _sid(t)
                if sid in incoming_ids:
                    continue                  # duplicate within this sync — first wins
                incoming_ids.add(sid)
                meta = [t.get(c) for c in self._PT_META_COLS]
                prior = existing.get(sid)
                if prior is not None:
                    set_clause = ", ".join(f"{c}=?" for c in self._PT_META_COLS)
                    revive = ", removed_at=NULL, is_new=1" if prior["removed_at"] else ""
                    conn.execute(
                        f"UPDATE playlist_tracks SET {set_clause}{revive} WHERE id=?",
                        (*meta, prior["id"]),
                    )
                else:
                    conn.execute(f"""
                        INSERT INTO playlist_tracks
                            (playlist_id, source_track_id, {", ".join(self._PT_META_COLS)},
                             match_status, matched_file_path, first_seen_at, is_new)
                        VALUES (?, ?, {", ".join("?" * len(self._PT_META_COLS))}, ?, ?, ?, 1)
                    """, (playlist_id, sid, *meta,
                          t.get("match_status", "unknown"), t.get("matched_file_path"), now))
                    added += 1

            removed = 0
            for sid, r in existing.items():
                if sid in incoming_ids:
                    continue
                if r["removed_at"]:               # tombstone survived a full sync → clear
                    conn.execute("DELETE FROM playlist_tracks WHERE id = ?", (r["id"],))
                else:
                    conn.execute("UPDATE playlist_tracks SET removed_at=? WHERE id=?",
                                 (now, r["id"]))
                    removed += 1
            conn.commit()
        return added, removed

    def mark_playlist_seen(self, playlist_id: int) -> None:
        """Clear the is_new badges for a playlist — called when its detail is viewed."""
        with self._connect() as conn:
            conn.execute("UPDATE playlist_tracks SET is_new = 0 WHERE playlist_id = ?",
                         (playlist_id,))
            conn.commit()

    def get_playlist_track_rows(self, playlist_id: int,
                                include_removed: bool = False) -> list[dict]:
        """Live playlist track rows (tombstones excluded unless include_removed).
        Feeds matching and m3u export, which must ignore removed tracks."""
        where = "" if include_removed else " AND removed_at IS NULL"
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM playlist_tracks WHERE playlist_id = ?{where} ORDER BY position",
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

    def _refresh_local_track_count(self, conn, playlist_id: int) -> None:
        """Keep playlists.track_count in step with the live row count. Sync-based
        sources stamp track_count from the source's reported total; Local has no
        source, so its 'total' is simply however many rows the user has added."""
        conn.execute(
            "UPDATE playlists SET track_count = "
            "(SELECT COUNT(*) FROM playlist_tracks WHERE playlist_id = ? AND removed_at IS NULL) "
            "WHERE id = ?", (playlist_id, playlist_id))

    def add_track_to_local_playlist(self, playlist_id: int, file_path: str) -> bool:
        """Add a library track to a Local playlist as a directly-'have' row — a local
        track is by definition present, so match_status/matched_file_path are set here
        without any matching pass. The track's own file_path is the stable per-source id
        (dedupe key), so re-adding the same track is a no-op. Returns True if a row was
        inserted, False if it was already in the playlist. Raises ValueError if the
        file isn't a known, non-deleted library track."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            t = conn.execute(
                "SELECT * FROM tracks WHERE file_path = ? AND status != 'deleted'",
                (file_path,)).fetchone()
            if t is None:
                raise ValueError("track not found")
            position = conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 AS n FROM playlist_tracks "
                "WHERE playlist_id = ?", (playlist_id,)).fetchone()["n"]
            # INSERT ... WHERE NOT EXISTS makes the add idempotent even if two clicks
            # race — a second add for the same (playlist, file_path) inserts nothing.
            cur = conn.execute("""
                INSERT INTO playlist_tracks
                    (playlist_id, source_track_id, spotify_track_id, position, title, artist,
                     album, album_artist, duration_ms, isrc, track_no, disc_no, year, cover_url,
                     added_at, norm_title, norm_artist, match_status, matched_file_path,
                     first_seen_at, is_new, removed_at)
                SELECT ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, 'have', ?, ?, 0, NULL
                WHERE NOT EXISTS (
                    SELECT 1 FROM playlist_tracks WHERE playlist_id = ? AND source_track_id = ?)
            """, (playlist_id, file_path, position, t["title"], t["artist"], t["album"],
                  t["album_artist"], t["duration_ms"], t["isrc"], t["track_no"], t["disc_no"],
                  t["year"], now, t["norm_title"], t["norm_artist"], file_path, now,
                  playlist_id, file_path))
            inserted = cur.rowcount > 0
            if inserted:
                self._refresh_local_track_count(conn, playlist_id)
            conn.commit()
            return inserted

    def add_tracks_to_local_playlist(self, playlist_id: int, file_paths: list[str]) -> dict:
        """Append a list of library paths to a Local playlist, in the given order.

        The bulk sibling of add_track_to_local_playlist, and the same idempotent
        INSERT … WHERE NOT EXISTS keyed on (playlist_id, source_track_id =
        file_path) — but in one connection and one transaction, where looping the
        single-track version would open a connection per path. A path with no
        live library row counts as skipped_missing rather than raising, since a
        bulk caller (saving a whole run queue) shouldn't lose the other 40 tracks
        to one stale entry. Returns {added, already_present, skipped_missing}."""
        now = datetime.now(timezone.utc).isoformat()
        added = already = skipped = 0
        with self._connect() as conn:
            position = conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 AS n FROM playlist_tracks "
                "WHERE playlist_id = ?", (playlist_id,)).fetchone()["n"]
            for path in file_paths:
                t = conn.execute(
                    "SELECT * FROM tracks WHERE file_path = ? AND status != 'deleted'",
                    (path,)).fetchone()
                if t is None:
                    skipped += 1
                    continue
                cur = conn.execute("""
                    INSERT INTO playlist_tracks
                        (playlist_id, source_track_id, spotify_track_id, position, title, artist,
                         album, album_artist, duration_ms, isrc, track_no, disc_no, year, cover_url,
                         added_at, norm_title, norm_artist, match_status, matched_file_path,
                         first_seen_at, is_new, removed_at)
                    SELECT ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, 'have', ?, ?, 0, NULL
                    WHERE NOT EXISTS (
                        SELECT 1 FROM playlist_tracks WHERE playlist_id = ? AND source_track_id = ?)
                """, (playlist_id, path, position, t["title"], t["artist"], t["album"],
                      t["album_artist"], t["duration_ms"], t["isrc"], t["track_no"], t["disc_no"],
                      t["year"], now, t["norm_title"], t["norm_artist"], path, now,
                      playlist_id, path))
                if cur.rowcount > 0:
                    added += 1
                    position += 1
                else:
                    already += 1
            if added:
                self._refresh_local_track_count(conn, playlist_id)
            conn.commit()
        return {"added": added, "already_present": already, "skipped_missing": skipped}

    def import_playlist_tracks(self, dest_id: int, src_id: int) -> dict:
        """Copy every library-backed ('have') track from one playlist into a Local
        playlist, skipping duplicates. Any source (Spotify / Navidrome / Local) may
        be the origin; only rows whose matched_file_path resolves to a live library
        track can be copied — a Local playlist row *is* a library file, so a source
        row that's merely 'missing'/'queued' has no file to add and is reported under
        skipped_missing rather than inserted.

        Runs in one transaction (unlike looping add_track_to_local_playlist, which
        opens a connection per call). Returns
        {added, already_present, skipped_missing}. The dest track_count is refreshed
        once at the end."""
        now = datetime.now(timezone.utc).isoformat()
        added = already = skipped = 0
        with self._connect() as conn:
            src_rows = conn.execute(
                "SELECT * FROM playlist_tracks WHERE playlist_id = ? AND removed_at IS NULL "
                "ORDER BY position", (src_id,)).fetchall()
            position = conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 AS n FROM playlist_tracks "
                "WHERE playlist_id = ?", (dest_id,)).fetchone()["n"]
            for r in src_rows:
                path = r["matched_file_path"]
                if r["match_status"] != "have" or not path:
                    skipped += 1
                    continue
                t = conn.execute(
                    "SELECT * FROM tracks WHERE file_path = ? AND status != 'deleted'",
                    (path,)).fetchone()
                if t is None:
                    skipped += 1
                    continue
                # Same idempotent INSERT the single add uses: source_track_id = the
                # library file_path, so re-importing (or a track already added by hand)
                # inserts nothing and counts as already_present.
                cur = conn.execute("""
                    INSERT INTO playlist_tracks
                        (playlist_id, source_track_id, spotify_track_id, position, title, artist,
                         album, album_artist, duration_ms, isrc, track_no, disc_no, year, cover_url,
                         added_at, norm_title, norm_artist, match_status, matched_file_path,
                         first_seen_at, is_new, removed_at)
                    SELECT ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, 'have', ?, ?, 0, NULL
                    WHERE NOT EXISTS (
                        SELECT 1 FROM playlist_tracks WHERE playlist_id = ? AND source_track_id = ?)
                """, (dest_id, path, position, t["title"], t["artist"], t["album"],
                      t["album_artist"], t["duration_ms"], t["isrc"], t["track_no"], t["disc_no"],
                      t["year"], now, t["norm_title"], t["norm_artist"], path, now,
                      dest_id, path))
                if cur.rowcount > 0:
                    added += 1
                    position += 1
                else:
                    already += 1
            if added:
                self._refresh_local_track_count(conn, dest_id)
            conn.commit()
        return {"added": added, "already_present": already, "skipped_missing": skipped}

    def reorder_local_playlist(self, playlist_id: int, ordered_ids: list[int]) -> bool:
        """Rewrite every live row's position from the given complete id order.

        Full-order rather than a per-row move: positions are only meaningful
        relative to each other, and one atomic rewrite can't leave the playlist
        half-reordered if the client dies mid-drag. The caller must send exactly
        the live row ids — no more, no fewer — so a stale client that missed a
        concurrent add or remove is rejected (False) instead of silently
        dropping or duplicating positions. Local playlists hard-delete rather
        than tombstone, but the live filter is applied anyway so the invariant
        doesn't depend on that."""
        with self._connect() as conn:
            live = [r["id"] for r in conn.execute(
                "SELECT id FROM playlist_tracks WHERE playlist_id = ? AND removed_at IS NULL",
                (playlist_id,)).fetchall()]
            if len(ordered_ids) != len(live) or set(ordered_ids) != set(live):
                return False
            conn.executemany(
                "UPDATE playlist_tracks SET position = ? WHERE id = ?",
                [(pos, pt_id) for pos, pt_id in enumerate(ordered_ids)])
            conn.commit()
        return True

    def remove_playlist_track(self, pt_id: int) -> None:
        """Hard-delete one playlist_tracks row (Local playlists: removal is an explicit
        delete, not a sync tombstone). Refreshes the owning playlist's track_count."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT playlist_id FROM playlist_tracks WHERE id = ?", (pt_id,)).fetchone()
            if row is None:
                return
            conn.execute("DELETE FROM playlist_tracks WHERE id = ?", (pt_id,))
            self._refresh_local_track_count(conn, row["playlist_id"])
            conn.commit()

    # ── Matched-track rollup (the detail page's stats strip) ──────────────────
    #
    # "Matched" = live (non-tombstone) rows whose match_status is 'have' and whose
    # matched_file_path still resolves to a non-deleted library row. Deduped by
    # file_path with the same GROUP BY the run-candidate query uses, so two source
    # rows pointing at one file count once. Unlike _PLAYLIST_RUN_JOIN (tracks.py)
    # this keeps un-analyzed and disliked tracks: the strip answers "what of this
    # playlist is on disk", not "what's runnable" — the per-preset counts do that.
    _PLAYLIST_MATCHED_JOIN = (
        "FROM playlist_tracks pt JOIN tracks t ON t.file_path = pt.matched_file_path "
        "WHERE pt.playlist_id = ? AND pt.removed_at IS NULL AND pt.match_status = 'have' "
        "AND t.status != 'deleted'"
    )

    def get_playlist_matched_rows(self, playlist_id: int) -> list[dict]:
        """One row per distinct matched library file, carrying just the fields the
        stats strip rolls up (runtime, BPM buckets, plays) plus the identity
        columns the playlist operations match on.

        Duration falls back to the source row's when the library row has none, so
        a library indexed before duration tagging still totals something sane;
        MAX() picks it deterministically across the collapsed duplicate rows.

        `album_artist` / `isrc` / `norm_artist` / `norm_title` are the library
        track's own, not the source row's — they exist so diff/merge/split can
        run the identity chain (file → ISRC → normalized artist+title) and group
        by album artist off this one canonical membership query. Every column is
        from `tracks`, so grouping by file_path collapses duplicate source rows
        without picking between differing values."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT t.file_path, t.title, t.artist, t.album_artist, t.bpm, "
                "t.isrc, t.norm_artist, t.norm_title, "
                "COALESCE(t.duration_ms, MAX(pt.duration_ms)) AS duration_ms, "
                "COALESCE(t.play_count, 0) AS play_count "
                + self._PLAYLIST_MATCHED_JOIN + " GROUP BY t.file_path",
                (playlist_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def find_local_playlist_by_name(self, name: str) -> Optional[dict]:
        """The Local playlist with this exact name (case-insensitively), or None.

        Split writes into `<playlist> · <group>` playlists and must be re-runnable
        without piling up near-duplicates, so it looks the name up first and tops
        the existing one up instead of creating a second. Local-only: a synced
        mirror's name belongs to its source and could be rewritten out from under
        us on the next sync. Lowest id wins if a user has hand-made two."""
        if not name:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM playlists WHERE source = 'local' AND name = ? COLLATE NOCASE "
                "ORDER BY id LIMIT 1", (name,)).fetchone()
        return dict(row) if row else None

    def get_playlist_last_change(self, playlist_id: int) -> Optional[str]:
        """When this playlist's membership last changed, from the timestamps the
        schema already carries — no new column.

        `first_seen_at` marks a row appearing (written by both the sync diff and a
        Local add) and `removed_at` marks a sync tombstone; the later of the two
        is the last observable change. A Local removal hard-deletes rather than
        tombstoning, so it leaves no mark — callers show this beside
        `last_synced_at` rather than treating it as a complete audit trail.
        Returns None for a playlist whose rows predate both stamps."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(first_seen_at) AS added, MAX(removed_at) AS removed "
                "FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,)).fetchone()
        stamps = [s for s in (row["added"], row["removed"]) if s]
        return max(stamps) if stamps else None

    def _queued_sids(self, conn) -> set:
        return {r["spotify_track_id"] for r in conn.execute(
            f"SELECT DISTINCT spotify_track_id FROM grab_queue "
            f"WHERE status IN ({','.join('?' * len(GRAB_NONTERMINAL))})",
            GRAB_NONTERMINAL,
        ).fetchall()}

    def _queued_pt_ids(self, conn) -> set:
        """playlist_track_ids with a non-terminal grab in flight — lets a queued
        non-Spotify track (no spotify_track_id) still read as 'queued' on its
        playlist row, via the grab's playlist_track_id link (Phase 5)."""
        return {r["playlist_track_id"] for r in conn.execute(
            f"SELECT DISTINCT playlist_track_id FROM grab_queue "
            f"WHERE playlist_track_id IS NOT NULL "
            f"AND status IN ({','.join('?' * len(GRAB_NONTERMINAL))})",
            GRAB_NONTERMINAL,
        ).fetchall()}

    def get_playlist_tracks(self, playlist_id: int, status: str = "") -> list[dict]:
        """Playlist tracks with a derived per-row status (have|queued|missing|removed).

        Tombstones (removed_at set) get derived_status 'removed' so they never match
        the coverage filters and only appear in the unfiltered detail view."""
        with self._connect() as conn:
            # LEFT JOIN the matched local file so 'have' rows carry the library
            # track's real BPM / duration / loudness / (library) artist+album —
            # used by the detail page to show run-readiness, link into the
            # track/artist/album pages, and to build a playback queue that gets
            # the same volume levelling as album and library playback (the
            # loudness feeds PlayerTrack.loudnessLufs). NULL on unmatched rows.
            rows = [dict(r) for r in conn.execute(
                "SELECT pt.*, t.bpm AS local_bpm, t.duration_ms AS local_duration_ms, "
                "t.detector AS local_detector, t.artist AS local_artist, "
                "t.album AS local_album, t.album_artist AS local_album_artist, "
                "t.loudness_lufs AS local_loudness_lufs, "
                # The joined file's identity + flags, for consumers that build a
                # playback queue straight from this listing (the Listen player):
                # local_file_path being non-NULL proves the join actually matched
                # a live library row (matched_file_path alone can be stale).
                "t.file_path AS local_file_path, t.starred AS local_starred, "
                "t.disliked AS local_disliked, "
                # Local play count (Navidrome-merged), so a matched row can show
                # how often it's been played next to its BPM and length.
                "t.play_count AS local_play_count "
                "FROM playlist_tracks pt "
                "LEFT JOIN tracks t ON t.file_path = pt.matched_file_path AND t.status != 'deleted' "
                "WHERE pt.playlist_id = ? ORDER BY pt.position",
                (playlist_id,),
            ).fetchall()]
            queued = self._queued_sids(conn)
            queued_pt = self._queued_pt_ids(conn)
        for r in rows:
            if r["removed_at"]:
                r["derived_status"] = "removed"
            elif r["spotify_track_id"] in queued or r["id"] in queued_pt:
                # A non-Spotify track (Navidrome) has no spotify_track_id to match on,
                # so also honour the playlist_track_id link the grab carries (Phase 5).
                r["derived_status"] = "queued"
            elif r["match_status"] == "have":
                r["derived_status"] = "have"
            else:
                r["derived_status"] = "missing"
        if status:
            rows = [r for r in rows if r["derived_status"] == status]
        return rows
