# Plans — unified status

One page tracking every plan in `docs/plans/`: what shipped, what's open, and loose
follow-ups that never got their own plan doc. **Update this file whenever a plan phase
ships or a new plan lands.** Last updated: 2026-07-19 (inbox candidate previews shipped, Unreleased).

## Overview

| Plan | Doc | Status |
|---|---|---|
| Suggestions page + Related panels + previews + queue-similar | [suggestions-page.md](suggestions-page.md) | ✅ Done (v2.5.0 / v2.5.1) |
| Navidrome star sync | [navidrome-star-sync.md](navidrome-star-sync.md) | ✅ Done (v2.5.2); follow-ups open |
| Multi-source playlists + Run-mode integration | [playlists-integration.md](playlists-integration.md) | ✅ Phases 1–5 done |
| Inbox candidate previews | [inbox-candidate-previews.md](inbox-candidate-previews.md) | 🔶 Part A done (Unreleased); Part B next |

---

## Suggestions page (✅ complete)

All four parts shipped:

- **Part A** — `/suggestions` page: Deezer-derived artist/track suggestions seeded from
  the library (starred-weighted), dismissals, add-to-queue (v2.5.0).
- **Part B** — Related panel on Artist / Album / Track pages via `/api/related/*`
  (v2.5.0).
- **Part C** — 30 s Deezer previews through the ducking player
  (`PlayerTrack.src`/`ephemeral`, `PreviewButton`) (v2.5.0).
- **Part D** — "Queue similar" on the player bar + Run page (in-library → play queue,
  cadence-folded on Run; missing → grab queue) (v2.5.1).

Open: nothing planned. Ideas parked in the backlog below.

## Navidrome star sync (✅ complete, follow-ups open)

- ✅ Two-way star sync (`integrations/star_sync.py`, `POST /api/settings/sync-stars`,
  Settings toggle + button) — v2.5.2.
- ✅ Scrobbling at the 50 % mark (`NAVIDROME_SCROBBLE`, `POST /api/scrobble`) and one-way
  play-count pull (`integrations/play_sync.py`, `run_prefer_familiar`) — v2.6.0.

Open follow-ups (no plan doc yet):

- ✅ **Periodic/background sync** for stars *and* play counts — shipped with playlists
  Phase 5 as a single `PeriodicSync` scheduler (`integrations/periodic_sync.py`, owned by
  `main.py` so it runs with the grabber off). One `sync_interval_minutes` drives playlist
  sync, star sync, and play-count pulls; each job self-gates on its own toggle/creds.
- ⬜ **`starred_at` timestamp column** for a real newest-wins conflict policy. Low
  priority: with the boolean-flag + per-track-baseline design, conflicts are
  mathematically unreachable, so this only matters if sync semantics ever change.

## Multi-source playlists (🔶 in progress)

- ✅ **Phase 1** — schema generalization (`source` column, `playlists` table rebuild),
  diff/upsert sync with new/removed tombstones (2026-07-17).
- ✅ **Phase 2** — Navidrome source: `getPlaylists`/`getPlaylist`, add-by-pick,
  metadata-matched coverage, new/removed badges.
- ✅ **Phase 3** — Run-mode integration: `playlist=` scope on `/api/run/queue`,
  player-readable `/api/run/playlists`, Run-page source picker. Refined through v2.6.6 –
  v2.6.9 (playlist-scoped refill, playlist top-up, playlist-vs-library behavior).
- ✅ **Phase 4** — **Local playlists + "Add to playlist"** (2026-07-19): create a Local
  playlist by name, add library tracks from track pages and Library rows (each add is
  directly `have`), remove tracks / delete the playlist. Grabber-independent; reuses the
  existing coverage / m3u / Run-source machinery unchanged. (The Run *player* was left
  without an add button by decision.)
- ✅ **Phase 5** — **Per-user access via local player users** (2026-07-19): `players` +
  `player_playlists` tables, username login (`RUN_PASSWORD` kept as a shared full-access
  guest), Settings → Player Access → Run users panel, playlist-filtered
  `/api/run/playlists`, membership + library/starred gating on `/api/run/queue`. Plus the
  **"play everything, force tempo"** run toggle, the **background sync scheduler**
  (`integrations/periodic_sync.py`), and source-agnostic **"queue missing"** via a shared
  `grabber/enqueue.py::enqueue_track` (normalized dedupe folded into `enqueue_grab`).
- ✅ **"Queue missing in grabber" now source-agnostic** — a missing **Navidrome** track
  enqueues by metadata (adopting a confident Spotify match's id when connected) and reads
  as *queued* on its playlist via the `playlist_track_id` link. Shipped with Phase 5.

## Inbox candidate previews (🔶 Part A done, Unreleased)

Plan: [inbox-candidate-previews.md](inbox-candidate-previews.md) (2026-07-19). 30 s
Deezer previews on inbox candidate cards (lazy URL resolution, no schema change), an
external link for yt-dlp candidates, and an optional source-track preview via Deezer
ISRC lookup. Reuses the Part C player/preview infrastructure end to end.

- ✅ Part A — candidate previews (`GET /api/inbox/candidates/<id>/preview` + `PreviewButton`
  lazy `resolveUrl`; `deezer_catalog.track_preview_url`).
- ⬜ Part B — source-track preview via `track/isrc:{ISRC}` (optional, separate commit).

Built against the v2.6.14 layout: the plan's `db.py` anchors are now the `db/` package
(no schema change either way), and the TTL cache follows `web/api/suggestions.py`'s
lock-guarded `time.monotonic()` style rather than the plan's bare-dict sketch.

---

## Ideas backlog (explicitly out of scope of their plans — unscheduled)

From **suggestions-page.md**:
- Last.fm / ListenBrainz as additional similarity sources (engine is source-agnostic).
- Re-fetching a stale Deezer preview URL on playback error.
- Preview buttons on Spotify search results via Deezer ISRC cross-lookup.
- "Grab all top tracks of this artist" bulk action.
- ntfy digest ("12 new suggestions this week").

From **inbox-candidate-previews.md**:
- Waveform/segment comparison of candidate vs source (the Duplicates → Compare
  analogue for remote audio).
- Previews on Queue-page rows (pending/failed items).

From the Run mode / PWA work (shipped v2.4.2 → v2.6.9, predates `docs/plans/`):
- Offline caching (deliberately out of scope for the PWA).
- Server-side ffmpeg `atempo` / Web Audio time-stretch (rejected; `playbackRate` chosen).
