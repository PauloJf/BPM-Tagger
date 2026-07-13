"""Tiny shared rate limiter for keyless public APIs.

Deezer's public API allows 50 requests per 5 seconds per IP; exceeding it
returns "Quota limit exceeded" errors, which the artist-image path would
mis-record as daily miss markers. One process-wide limiter caps every
api.deezer.com call (artist-image auto-fetch, image-picker search) well under
that quota, so a burst of concurrent requests queues briefly instead of
tripping it.
"""

import threading
import time
from collections import deque


class RateLimiter:
    """Sliding-window limiter: at most ``max_calls`` per ``period`` seconds.
    ``acquire()`` blocks (briefly) until a slot is free — safe from any thread."""

    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period = period
        self._calls: deque = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= self.period:
                    self._calls.popleft()
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return
                wait = self.period - (now - self._calls[0])
            time.sleep(min(max(wait, 0.01), 0.25))


# Half of Deezer's documented 50 req / 5 s, shared by all callers.
deezer_limiter = RateLimiter(max_calls=25, period=5.0)
