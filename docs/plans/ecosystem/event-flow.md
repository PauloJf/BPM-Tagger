# Event flow between components

Status: **accepted as recommended** (2026-11-10), see [decisions.md](decisions.md).

Constraints: no shared DB, no Python imports, and the recorded "no webhooks" decision (SSRF
surface; `integration-and-suggestions.md`). Today the gaps are filled by polling: Music Grabber
re-walks the library on every sync and reads tags back once a minute; BPM Tagger batch-polls
request states; the only "scan now" path needs BPM Tagger's admin password.

## Options

| | (1) Pull feed with a cursor (long-poll) | (2) Signed webhooks | (3) Broker (Redis / NATS / MQTT) |
|---|---|---|---|
| Recorded decisions | Kept | Reverses "no webhooks" | Kept |
| Delivery when a service was down | Catches up from its cursor | Needs retries + a dead-letter store | Depends on broker persistence |
| New infrastructure | None | None | One more service |
| Latency | ~0 while a long-poll is open | ~0 | ~0 |
| Effort | S-M | M | M-L |

## Recommendation

**(1).** Each service keeps an append-only `events` table and serves
`GET /api/v1/events?after=<cursor>&wait=<s>`; consumers store their cursor. Authenticated with the
service token from ADR-002. Old rows are pruned by age; a consumer whose cursor fell off the end
gets `reset: true` and does one full refresh (what it does today on every poll).

```
Music Grabber places a file in the music folder
  ├─ POST BPM Tagger /api/v1/intake {paths}        (hint; the watcher still catches it without)
  ▼
BPM Tagger: scan → BPM → loudness → lyrics → tag index
  ├─ event track.analysed {path, isrc, bpm, loudness, lyrics}
  ├─ event library.changed (coalesced)             → Navidrome rescan, if configured
  ▼
Music Grabber (long-poll on BPM Tagger's feed)
  ├─ updates its library cache and the queue item's history (replaces the 60 s read-back)
  ├─ runs the playlist mirror
  └─ event request.updated {id, state, path}
        ▼
BPM Tagger (long-poll on Music Grabber's feed): request rows update, the finished file is linked
to its playlist row, Subsonic clients and the players see the track.
```

The filesystem stays the source of truth: every event is a hint, and every consumer still
converges by its existing full refresh if the feed is unavailable or unconfigured.
