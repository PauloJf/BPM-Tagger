"""Connection, schema creation, and additive migrations."""

import logging
import os
import shutil
import sqlite3

from ..config import __version__
log = logging.getLogger(__name__)

class _DBBase:
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
        # Enforce foreign keys — off by default in SQLite and scoped per
        # connection, so it must be set on every connect. Schema setup/migration
        # deliberately turns it OFF (see _init_db).
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        with self._connect() as conn:
            # Schema creation + migration run with FK enforcement OFF: the
            # playlists rebuild (_migrate_playlists_schema) renames tables, and
            # with FKs on that would rewrite/refire relationships mid-migration.
            # Runtime connections keep FKs ON (see _connect).
            conn.execute("PRAGMA foreign_keys=OFF")
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
            # ── Lyrics ────────────────────────────────────────────────────────
            ("lyrics_status",  "TEXT"),     # embedded | fetched | not_found | instrumental
            ("lyrics_synced",  "INTEGER DEFAULT 0"),
            # ── Run mode ──────────────────────────────────────────────────────
            ("starred",        "INTEGER DEFAULT 0"),
            ("disliked",       "INTEGER DEFAULT 0"),
            # ── Navidrome star sync ───────────────────────────────────────────
            ("nd_song_id",     "TEXT"),               # cached Subsonic song id
            ("starred_base",   "INTEGER DEFAULT 0"),  # remote 'starred' at last sync (baseline)
            # ── Navidrome play counts (pulled; NULL = never pulled) ───────────
            ("play_count",     "INTEGER"),
            ("last_played",    "TEXT"),               # OpenSubsonic 'played' timestamp
        ]:
            if col not in existing:
                conn.execute(f"ALTER TABLE tracks ADD COLUMN {col} {coldef}")
        # Pre-v1.0.4 lock_track() didn't clear needs_review; fix stale rows.
        conn.execute(
            "UPDATE tracks SET needs_review = 0 WHERE locked = 1 AND needs_review = 1"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_norm ON tracks(norm_artist, norm_title)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_isrc ON tracks(isrc)")
        # Exact grabbed-track identity: library_match matches a grabbed file back
        # to its Spotify track by this, independent of ISRC/fuzzy and of the queue.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_sid ON tracks(spotify_track_id)")

        # Run-mode usage counters (cumulative key/value totals; see add_run_stats).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS run_stats (
                key   TEXT PRIMARY KEY,
                value REAL NOT NULL DEFAULT 0
            )
        """)

        self._migrate_playlists_schema(conn)
        self._create_grabber_tables(conn)
        self._migrate_playlists_schema(conn, finish=True)

        # Orphan sweep: DBs created before FK enforcement have no ON DELETE
        # CASCADE (added to the CREATE statements above, so only fresh DBs get
        # it). Clean up any child rows an older DB left behind when a parent was
        # removed. Cheap and idempotent — safe to run on every start.
        conn.execute("DELETE FROM grab_candidates "
                     "WHERE queue_item_id NOT IN (SELECT id FROM grab_queue)")
        conn.execute("DELETE FROM grab_events "
                     "WHERE queue_item_id NOT IN (SELECT id FROM grab_queue)")
        conn.execute("DELETE FROM playlist_tracks "
                     "WHERE playlist_id NOT IN (SELECT id FROM playlists)")

    def _migrate_playlists_schema(self, conn, finish: bool = False):
        """Generalize the Spotify-only playlists / playlist_tracks tables to the
        multi-source shape (adds `source`, relaxes `spotify_id NOT NULL/UNIQUE`,
        adds membership columns). SQLite can't drop a NOT NULL/UNIQUE in place, so
        this rebuilds via rename → recreate → copy → drop, preserving row ids (and
        thus grab_queue.playlist_track_id references).

        Called twice around _create_grabber_tables: the first pass renames legacy
        tables out of the way so the recreate makes the new schema; the ``finish``
        pass copies the old rows in and drops the temporaries."""
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not finish:
            if "playlists" in tables and "source" not in {
                    row[1] for row in conn.execute("PRAGMA table_info(playlists)")}:
                conn.execute("ALTER TABLE playlists RENAME TO _playlists_old")
            if "playlist_tracks" in tables and "source_track_id" not in {
                    row[1] for row in conn.execute("PRAGMA table_info(playlist_tracks)")}:
                conn.execute("ALTER TABLE playlist_tracks RENAME TO _playlist_tracks_old")
            return
        if "_playlists_old" in tables:
            conn.execute("""
                INSERT INTO playlists
                    (id, source, spotify_id, navidrome_id, name, snapshot_id, enabled,
                     image_url, track_count, last_synced_at, created_at)
                SELECT id, 'spotify', spotify_id, NULL, name, snapshot_id, enabled,
                       image_url, track_count, last_synced_at, created_at
                FROM _playlists_old
            """)
            conn.execute("DROP TABLE _playlists_old")
        if "_playlist_tracks_old" in tables:
            conn.execute("""
                INSERT INTO playlist_tracks
                    (id, playlist_id, source_track_id, spotify_track_id, position, title,
                     artist, album, album_artist, duration_ms, isrc, track_no, disc_no,
                     year, cover_url, added_at, norm_title, norm_artist, match_status,
                     matched_file_path, first_seen_at, is_new, removed_at)
                SELECT id, playlist_id, spotify_track_id, spotify_track_id, position, title,
                       artist, album, album_artist, duration_ms, isrc, track_no, disc_no,
                       year, cover_url, added_at, norm_title, norm_artist, match_status,
                       matched_file_path, added_at, 0, NULL
                FROM _playlist_tracks_old
            """)
            conn.execute("DROP TABLE _playlist_tracks_old")

    def _create_grabber_tables(self, conn):
        """New grabber tables (§2). All CREATE ... IF NOT EXISTS — safe to re-run."""
        # `source` generalizes playlists beyond Spotify (navidrome | local). spotify_id
        # is nullable now (only Spotify rows carry one); NULLs are distinct under the
        # UNIQUE index, so many non-Spotify rows coexist. Older DBs are rebuilt into
        # this shape by _migrate_playlists_schema().
        conn.execute("""
            CREATE TABLE IF NOT EXISTS playlists (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                source         TEXT NOT NULL DEFAULT 'spotify',   -- spotify | navidrome | local
                spotify_id     TEXT UNIQUE,
                navidrome_id   TEXT UNIQUE,
                name           TEXT,
                snapshot_id    TEXT,
                enabled        INTEGER DEFAULT 1,
                image_url      TEXT,
                track_count    INTEGER DEFAULT 0,
                last_synced_at TEXT,
                created_at     TEXT
            )
        """)
        # Membership state (is_new / removed_at) is independent of local availability
        # (match_status). No UNIQUE(playlist_id, position) — positions are rewritten on
        # every sync; the diff keys on source_track_id in Python instead.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS playlist_tracks (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_id      INTEGER NOT NULL,
                source_track_id  TEXT,                     -- spotify track id / navidrome song id (diff key)
                spotify_track_id TEXT,                     -- kept for grabber back-compat
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
                first_seen_at    TEXT,                     -- when this row first appeared in the source
                is_new           INTEGER DEFAULT 0,        -- added since last viewed (cleared on view)
                removed_at       TEXT,                     -- tombstone: gone from source (NULL = present)
                FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pt_playlist ON playlist_tracks(playlist_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pt_sid ON playlist_tracks(spotify_track_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pt_src ON playlist_tracks(playlist_id, source_track_id)")
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
                rank             INTEGER,
                FOREIGN KEY (queue_item_id) REFERENCES grab_queue(id) ON DELETE CASCADE
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gc_item ON grab_candidates(queue_item_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS grab_events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                queue_item_id INTEGER NOT NULL,
                event         TEXT,
                detail        TEXT,
                created_at    TEXT,
                FOREIGN KEY (queue_item_id) REFERENCES grab_queue(id) ON DELETE CASCADE
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dismissed_dupes (
                signature TEXT PRIMARY KEY
            )
        """)
        # ── Suggestions (Deezer-derived artist/track recommendations) ─────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS suggestions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                kind         TEXT NOT NULL,            -- 'artist' | 'track'
                dz_id        TEXT NOT NULL,            -- deezer artist id or track id
                name         TEXT,                     -- artist name / track title
                artist       TEXT DEFAULT '',          -- tracks only
                album        TEXT DEFAULT '',
                duration_ms  INTEGER,
                image_url    TEXT DEFAULT '',
                preview_url  TEXT DEFAULT '',
                score        REAL DEFAULT 0,
                have_tracks  INTEGER DEFAULT 0,        -- artists only: library tracks owned (0-2; >=3 filtered)
                seeds        TEXT DEFAULT '[]',        -- JSON: seed artist names this came from
                computed_at  TEXT,
                queued_at    TEXT,                     -- set when user enqueues (until next refresh)
                UNIQUE(kind, dz_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sug_kind ON suggestions(kind)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS suggestion_dismissed (
                kind         TEXT NOT NULL,            -- 'artist' | 'track'
                key          TEXT NOT NULL,            -- artist: normalize_artist(name); track: dz track id
                dismissed_at TEXT,
                PRIMARY KEY (kind, key)
            )
        """)
