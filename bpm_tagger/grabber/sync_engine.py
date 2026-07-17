"""SpotifySync — background playlist→library reconciliation (§5).

Modeled on the scanner's drain loop: a 2s-tick thread that runs a full sync
every SPOTIFY_SYNC_MINUTES or when nudged via ``request_sync()``. Per enabled
playlist: refresh token → compare snapshot_id → (rebuild playlist_tracks) →
library-match every track. Auth failure marks disconnected and never crashes
the loop. GrabberService wraps the client + thread for the web layer to use.
"""

import logging
import threading
import time

from .matching import library_match
from .spotify import SpotifyAuthError, SpotifyClient, SpotifyError

log = logging.getLogger(__name__)


class SpotifySync(threading.Thread):
    def __init__(self, config, db, tagger, client, notifier=None):
        super().__init__(daemon=True, name="SpotifySync")
        self.config = config
        self.db = db
        self.tagger = tagger
        self.client = client
        self.notifier = notifier
        self._stop = threading.Event()
        self._sync_now = threading.Event()
        self._lock = threading.Lock()          # serialize sync_all vs manual sync
        self.last_error = ""
        self.last_synced_epoch = 0.0

    # ── control ───────────────────────────────────────────────────────────────
    def request_sync(self):
        self._sync_now.set()

    def stop(self):
        self._stop.set()
        self._sync_now.set()

    # ── loop ──────────────────────────────────────────────────────────────────
    def run(self):
        interval = max(60, int(self.config.get("spotify_sync_minutes", 30)) * 60)
        log.info("SpotifySync started (every %d min)", interval // 60)
        # Initial sync shortly after boot.
        self._sync_now.set()
        while not self._stop.is_set():
            due = (time.time() - self.last_synced_epoch) >= interval
            if self._sync_now.is_set() or due:
                self._sync_now.clear()
                try:
                    self.sync_all()
                except Exception as exc:  # never let the loop die
                    log.exception("SpotifySync cycle failed: %s", exc)
            self._stop.wait(2)

    # ── work ──────────────────────────────────────────────────────────────────
    def sync_all(self):
        if not self.client.is_connected():
            return
        with self._lock:
            self.tagger.index_tags()
            playlists = self.db.get_enabled_playlists()
            for pl in playlists:
                if self._stop.is_set():
                    break
                self._sync_one(pl)
            self.last_synced_epoch = time.time()
            self.last_error = ""

    def sync_playlist(self, playlist_id: int) -> dict:
        """Manual single-playlist sync (used by the API, works without the thread)."""
        pl = self.db.get_playlist(playlist_id)
        if not pl:
            raise SpotifyError("Playlist not found")
        with self._lock:
            self.tagger.index_tags()
            self._sync_one(pl)
        return self.db.get_playlist(playlist_id)

    def _sync_one(self, pl: dict):
        try:
            meta = self.client.get_playlist_meta(pl["spotify_id"])
            if meta["snapshot_id"] != (pl.get("snapshot_id") or ""):
                tracks = self.client.get_playlist_tracks(pl["spotify_id"])
                added, removed = self.db.sync_playlist_tracks(pl["id"], tracks)
                self.db.update_playlist_sync(pl["id"], meta["snapshot_id"], meta["name"],
                                             meta["image_url"], meta["track_count"])
                log.info("Playlist '%s': %d tracks (snapshot changed; +%d new, -%d removed)",
                         meta["name"], len(tracks), added, removed)
            # Always re-match: the library changes even when the playlist doesn't.
            self._match_playlist(pl["id"])
        except SpotifyAuthError as exc:
            self.last_error = str(exc)
            log.error("Spotify disconnected during sync: %s", exc)
            self.client.disconnect()
            if self.notifier:
                try:
                    self.notifier.send_grabber(
                        "Spotify disconnected",
                        "The Spotify connection expired — reconnect in Settings.",
                        priority="high", tags="warning",
                    )
                except Exception:
                    pass
            raise
        except SpotifyError as exc:
            self.last_error = str(exc)
            log.warning("Playlist sync error (%s): %s", pl.get("name"), exc)

    def _match_playlist(self, playlist_id: int):
        rows = self.db.get_playlist_track_rows(playlist_id)
        for pt in rows:
            path = library_match(pt, self.db)
            new_status = "have" if path else "missing"
            if new_status != pt.get("match_status") or path != pt.get("matched_file_path"):
                self.db.set_playlist_track_match(pt["id"], new_status, path)
            # Auto-enqueue freshly-missing tracks (skip ones already tried — failed/
            # skipped items are only retried via an explicit user action).
            if new_status == "missing" and pt.get("spotify_track_id") \
                    and not self.db.has_any_grab(pt["spotify_track_id"]):
                self.db.enqueue_grab(_grab_meta(pt))


def _grab_meta(pt: dict) -> dict:
    return {"playlist_track_id": pt.get("id"), "spotify_track_id": pt.get("spotify_track_id"),
            "title": pt.get("title"), "artist": pt.get("artist"), "album": pt.get("album"),
            "album_artist": pt.get("album_artist"), "duration_ms": pt.get("duration_ms"),
            "isrc": pt.get("isrc"), "track_no": pt.get("track_no"), "disc_no": pt.get("disc_no"),
            "year": pt.get("year"), "cover_url": pt.get("cover_url")}


class GrabberService:
    """Owns the Spotify client + sync thread; the web layer reaches grabber
    features through this. Present whenever grabber is enabled, regardless of
    MODE; the background thread only runs in watch modes."""

    def __init__(self, config, db, tagger, notifier=None):
        self.config = config
        self.db = db
        self.client = SpotifyClient(config, db)
        self.sync = SpotifySync(config, db, tagger, self.client, notifier)
        from .worker import GrabPool
        self.pool = GrabPool(config, db, tagger, notifier)
        from .suggestions import SuggestionsEngine
        self.suggestions = SuggestionsEngine(config, db)
        self._thread_started = False

    def start_background(self):
        if not self._thread_started:
            self.sync.start()
            self.pool.start()
            self._thread_started = True

    def stop_background(self):
        if self._thread_started:
            self.sync.stop()
            self.pool.stop()

    def enqueue_missing(self, playlist_id: int) -> int:
        """Manually enqueue every missing track of a playlist (re-attempts failed
        ones, unlike the sync auto-enqueue). Returns how many were enqueued."""
        n = 0
        for pt in self.db.get_playlist_tracks(playlist_id, "missing"):
            if pt.get("spotify_track_id") and not self.db.has_nonterminal_grab(pt["spotify_track_id"]):
                if self.db.enqueue_grab(_grab_meta(pt)) is not None:
                    n += 1
        self.sync.request_sync()
        return n

    # Convenience pass-throughs for the API layer.
    def status(self) -> dict:
        s = self.client.status()
        s["last_error"] = self.sync.last_error
        return s

    def authorize_url(self, state: str) -> str:
        return self.client.authorize_url(state)

    def handle_callback(self, code: str) -> None:
        self.client.exchange_code(code)
        self.sync.request_sync()

    def request_sync(self):
        self.sync.request_sync()
