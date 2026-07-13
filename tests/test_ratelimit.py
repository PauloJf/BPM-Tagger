"""RateLimiter — sliding-window blocking behaviour."""

import time

from bpm_tagger.integrations.ratelimit import RateLimiter


def test_burst_within_limit_does_not_block():
    rl = RateLimiter(max_calls=3, period=1.0)
    t0 = time.monotonic()
    for _ in range(3):
        rl.acquire()
    assert time.monotonic() - t0 < 0.2


def test_call_over_limit_waits_for_window():
    rl = RateLimiter(max_calls=2, period=0.5)
    t0 = time.monotonic()
    rl.acquire()
    rl.acquire()
    rl.acquire()  # must wait ~0.5 s for the first call to age out
    assert time.monotonic() - t0 >= 0.4
