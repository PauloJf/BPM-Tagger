# Plans — unified status

One page tracking every plan in `docs/plans/`: what shipped, what's open, and loose
follow-ups that never got their own plan doc. **Update this file whenever a plan phase
ships or a new plan lands.** Last updated: 2026-07-19 (v2.6.9).

## Overview

| Plan | Doc | Status |
|---|---|---|
| Suggestions page + Related panels + previews + queue-similar | [suggestions-page.md](suggestions-page.md) | ✅ Done (v2.5.0 / v2.5.1) |
| Navidrome star sync | [navidrome-star-sync.md](navidrome-star-sync.md) | ✅ Done (v2.5.2); follow-ups open |
| Multi-source playlists + Run-mode integration | [playlists-integration.md](playlists-integration.md) | 🔶 Phases 1–4 done; 5 open |
| Inbox candidate previews | [inbox-candidate-previews.md](inbox-candidate-previews.md) | ⬜ Planned, not started |

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

- ⬜ **Periodic/background sync** for stars *and* play counts — both are manual-button
  only today. Candidate approaches: piggyback on scan completion, or an interval thread
  like the grabber's `SpotifySync` loop (`grabber/sync_engine.py`).
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
- ⬜ **Phase 5** — **Per-user access via local player users** (decided 2026-07-19;
  supersedes the Navidrome-credential-login idea): `players` + `player_playlists`
  tables, Settings → Users admin panel, playlist-filtered `/api/run/playlists`,
  membership check on `/api/run/queue` (library/starred pools full-access-only),
  `RUN_PASSWORD` retired or kept as a shared guest user. Plus periodic playlist sync
  and the "play everything, force tempo" toggle.
  Rides along (decided 2026-07-19): the Phase 2 deferral below.
- ⬜ **"Queue missing in grabber" is Spotify-only** — `grab_queue` is keyed on
  `spotify_track_id`, so missing Navidrome tracks can't be queued yet. Bundled into
  Phase 5.

## Inbox candidate previews (⬜ not started)

Plan: [inbox-candidate-previews.md](inbox-candidate-previews.md) (2026-07-19). 30 s
Deezer previews on inbox candidate cards (lazy URL resolution, no schema change), an
external link for yt-dlp candidates, and an optional source-track preview via Deezer
ISRC lookup. Reuses the Part C player/preview infrastructure end to end.

- ⬜ Part A — candidate previews (endpoint + `PreviewButton` lazy `resolveUrl`).
- ⬜ Part B — source-track preview via `track/isrc:{ISRC}` (optional, separate commit).

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
