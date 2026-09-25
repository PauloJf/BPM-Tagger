"""Live registry of Subsonic clients: who is connected with which app, and what
each one is playing. In memory only — it describes the last hour, not history
(plays themselves are recorded by scrobble as usual), and a restart clears it.

What "playing" means, in order of trust:

1. **A now-playing report** — ``scrobble?submission=false``, which most apps
   send when a track starts. Authoritative.
2. **A stream request** — used only for apps that have never sent a
   now-playing report. Less reliable: some apps download upcoming tracks ahead
   of time, so a stream can be a pre-fetch. An app that sends even one
   now-playing report is never inferred from streams again.

A track stops counting as playing once its duration (plus a grace minute) has
passed since it started without a newer report.
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Optional

CLIENT_TTL = 3600          # forget a client after an hour without requests
PLAYING_GRACE = 60         # seconds past a track's end before it stops counting
_FALLBACK_DURATION = 600   # when a track has no known duration


@dataclass
class NowPlaying:
    track_id: int
    title: str
    artist: str
    album: str
    duration_s: Optional[int]
    started: float
    source: str                     # "report" | "stream"

    def active(self, now: float) -> bool:
        return now - self.started <= (self.duration_s or _FALLBACK_DURATION) + PLAYING_GRACE


@dataclass
class Client:
    owner: str
    username: str
    app: str
    version: str
    ip: str
    user_agent: str
    first_seen: float
    last_seen: float
    requests: int = 0
    reports_now_playing: bool = False
    playing: Optional[NowPlaying] = None
    last_played: Optional[NowPlaying] = field(default=None)


class Activity:
    def __init__(self):
        self._lock = threading.Lock()
        self._clients: dict[tuple, Client] = {}

    @staticmethod
    def _key(owner: str, app: str, ip: str) -> tuple:
        return (owner, app.lower(), ip)

    def _prune(self, now: float) -> None:
        for k in [k for k, c in self._clients.items() if now - c.last_seen > CLIENT_TTL]:
            del self._clients[k]

    def seen(self, owner: str, username: str, app: str, version: str,
             ip: str, user_agent: str) -> tuple:
        """Record one authenticated request; returns the client's key."""
        now = time.time()
        app = (app or "").strip()[:60] or "Unknown app"
        key = self._key(owner, app, ip)
        with self._lock:
            c = self._clients.get(key)
            if c is None:
                c = Client(owner, username, app, (version or "")[:20], ip,
                           (user_agent or "")[:200], now, now)
                self._clients[key] = c
            c.last_seen, c.requests = now, c.requests + 1
            if version:
                c.version = version[:20]
            self._prune(now)
        return key

    def _set_playing(self, key: tuple, track: dict, source: str) -> None:
        now = time.time()
        np = NowPlaying(
            track_id=track["id"],
            title=track.get("title") or "",
            artist=track.get("artist") or "",
            album=track.get("album") or "",
            duration_s=int((track.get("duration_ms") or 0) / 1000) or None,
            started=now, source=source)
        with self._lock:
            c = self._clients.get(key)
            if c is None:
                return
            if c.playing and c.playing.track_id != np.track_id:
                c.last_played = c.playing
            c.playing = np

    def now_playing_report(self, key: tuple, track: dict) -> None:
        with self._lock:
            c = self._clients.get(key)
            if c is not None:
                c.reports_now_playing = True
        self._set_playing(key, track, "report")

    def streamed(self, key: tuple, track: dict) -> None:
        """A stream request: counts as "playing" only for apps that never
        report now-playing themselves (see the module docstring)."""
        with self._lock:
            c = self._clients.get(key)
            if c is None or c.reports_now_playing:
                return
            if c.playing and c.playing.track_id == track["id"] and c.playing.active(time.time()):
                return  # a range/seek request for the same track, not a new play
        self._set_playing(key, track, "stream")

    def snapshot(self, owner: Optional[str] = None) -> list[dict]:
        """Clients seen in the last hour, most recent first. ``owner`` limits
        the list to one account's own clients."""
        now = time.time()
        out = []
        with self._lock:
            self._prune(now)
            for c in self._clients.values():
                if owner is not None and c.owner != owner:
                    continue
                playing = c.playing if c.playing and c.playing.active(now) else None
                last = None if playing else (c.playing or c.last_played)
                out.append({
                    "owner": c.owner, "username": c.username, "app": c.app,
                    "version": c.version, "ip": c.ip, "user_agent": c.user_agent,
                    "first_seen": c.first_seen, "last_seen": c.last_seen,
                    "requests": c.requests,
                    "playing": None if playing is None else {
                        "track_id": playing.track_id, "title": playing.title,
                        "artist": playing.artist, "album": playing.album,
                        "duration_s": playing.duration_s,
                        "elapsed_s": int(now - playing.started),
                        "source": playing.source,
                    },
                    "last_played": None if last is None else {
                        "track_id": last.track_id, "title": last.title,
                        "artist": last.artist, "ago_s": int(now - last.started),
                    },
                })
        out.sort(key=lambda c: c["last_seen"], reverse=True)
        return out


# The process-wide registry. (Not named `activity`: that would shadow this
# module on the package once __init__ imports it.)
registry = Activity()
