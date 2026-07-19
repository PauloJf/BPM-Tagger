"""PeriodicSync — one background scheduler for the otherwise manual-only syncs
(Phase 5, docs/plans/phase5-player-users.md §6).

Owned by ``main.py``, deliberately NOT by ``GrabberService``: Navidrome playlist sync
and star / play-count sync must run with the grabber disabled (the parent plan's
"decouple from the grabber" rule). A single interval thread runs whichever jobs are
enabled each tick:

  * **playlists** — enabled Spotify (needs grabber + live connection) and Navidrome
    (needs creds) playlists; **Local never syncs**. Each source self-gates; a failure
    on one playlist doesn't abort the tick.
  * **stars** — when ``navidrome_star_sync`` is on.
  * **play counts** — when Navidrome is configured.

``sync_interval_minutes = 0`` disables it (the thread is never started). Manual Sync
buttons are unaffected. This unifies the STATUS.md "periodic star/play-count sync"
follow-up with playlist sync so there is one loop, not three.
"""

import logging
import threading

log = logging.getLogger(__name__)

MIN_INTERVAL_MINUTES = 5   # floor — nothing here needs to run more often


class PeriodicSync(threading.Thread):
    def __init__(self, config, db, grabber=None):
        super().__init__(daemon=True, name="PeriodicSync")
        self.config = config
        self.db = db
        self.grabber = grabber
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        minutes = int(self.config.get("sync_interval_minutes", 0) or 0)
        if minutes <= 0:
            return
        interval = max(MIN_INTERVAL_MINUTES, minutes) * 60
        log.info("PeriodicSync started (every %d min)", interval // 60)
        # First tick after one interval — boot already runs the grabber's initial sync,
        # so let the app settle before the periodic pass.
        while not self._stop.wait(interval):
            try:
                self._tick()
            except Exception as exc:  # never let the loop die
                log.exception("PeriodicSync tick failed: %s", exc)

    def _tick(self):
        self._sync_playlists()
        self._sync_stars()
        self._pull_play_counts()

    def _sync_playlists(self):
        from .navidrome_playlists import navidrome_configured, sync_navidrome_playlist
        for pl in self.db.get_enabled_playlists():
            if self._stop.is_set():
                return
            source = pl.get("source") or "spotify"
            if source == "local":
                continue
            try:
                if source == "navidrome":
                    if navidrome_configured(self.config):
                        sync_navidrome_playlist(self.db, self.config, pl["id"])
                elif source == "spotify":
                    g = self.grabber
                    if g and g.client.is_connected():
                        g.sync.sync_playlist(pl["id"])
            except Exception as exc:
                log.warning("PeriodicSync: playlist '%s' sync failed: %s", pl.get("name"), exc)

    def _sync_stars(self):
        if not self.config.get("navidrome_star_sync"):
            return
        try:
            from .star_sync import sync_stars
            sync_stars(self.db, self.config)
        except Exception as exc:
            log.warning("PeriodicSync: star sync failed: %s", exc)

    def _pull_play_counts(self):
        from .navidrome_playlists import navidrome_configured
        if not navidrome_configured(self.config):
            return
        try:
            from .play_sync import pull_play_counts
            pull_play_counts(self.db, self.config)
        except Exception as exc:
            log.warning("PeriodicSync: play-count pull failed: %s", exc)
