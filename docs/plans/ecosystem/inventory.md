# Ecosystem inventory (audit of 2026-11-10)

BPM Tagger v2.21.0 (`f174b81`) and Music Grabber v1.6.0 (`1798426`). BT = BPM Tagger, MG = Music
Grabber, ND = Navidrome. This file is in a public repo, so MG is described by capability only;
its file references and mechanics are in MG's private `docs/plans/ecosystem/inventory-private.md`.

## Capability matrix

| # | Target | BPM Tagger | Music Grabber | Navidrome | Missing |
|---|---|---|---|---|---|
| 1 | **Accounts and roles** | Row-less admin (`web/auth.py:27`, TOTP `web/totp.py:24`); `players` table with `player` / `member` roles (`db/base.py:178-198`); default-deny role lists (`web/app.py:71,117`); cookie session + CSRF (`web/auth.py:21,123`) | Own `users` + `sessions` (roles admin / user), bearer token, scoped integration keys | Own users | **One account system.** Three today, none linked |
| 1 | **Per-user data** | Per account: ratings + dislikes (`db/base.py:288`, `db/ratings.py`), play events (`:249`), runs (`:228`), queue snapshot (`:212`), Subsonic play queue (`:549`). **Global:** playlists have no owner (`:437`; players scoped via `player_playlists` `:200`), Subsonic album / artist stars (`:518`), `tracks.play_count` | Per user: artist follows / hides, dismissals. Global: watched playlists, the Spotify connection, caches | Per ND user; BT syncs the admin's stars / ratings only | Playlist ownership; per-owner album / artist stars; follows keyed to the same account as ratings |
| 2 | **BPM detection + tagging** | deeprhythm / essentia / librosa (`bpm/detectors.py:34,53,89`); `_reconcile` picks the in-range value on a ~2x split (`bpm/pipeline.py:111-126`); `_normalize_bpm` folds into range (`:165`) | none (by design) | reads the tag | **No evaluation harness or ground truth anywhere.** Octave choice is range-based only; no variable-tempo detection |
| 3 | **Lyrics** | LRCLIB fetch, synced when available (`integrations/lrclib.py:68`); written as LRC text into the *unsynced* tag fields or a `.lrc` sidecar (`bpm/lyrics.py:1-8,115`); bulk fill (`web/api/lyrics.py:121-190`); Subsonic `getLyricsBySongId` | Same fetcher, embeds at download time | reads tags | True synced frames (ID3 `SYLT`) are not written; two fetchers |
| 3 | **Loudness** | Integrated LUFS measured and stored (`bpm/loudness.py:120`), existing RG / R128 tags read (`:74`); web player attenuates (`frontend/src/lib/player.tsx:28-38`) | reads back what BT stored | reads RG tags | **ReplayGain / R128 tags are never written; Subsonic songs carry no `replayGain`** (`web/subsonic/views.py:40`) |
| 4 | **Listen player (weighted)** | Shared sampler (`web/weighting.py:29-123`), per-account weights, dislikes excluded, new-song multiplier; Listen queue / radio (`web/api/listen.py:170,204`) | none | basic | nothing structural |
| 4 | **Run player (tempo lock)** | Octave-folded queue + weighted draw (`web/api/run.py:52-200`); `playbackRate` lock in the SPA; offline preload (`frontend/src/lib/offline.ts`, `public/sw.js`) | none | none | Screen-off reliability on iOS; cadence sensors (ADR-005) |
| 5 | **Similar songs / artists** | Offline similar (`web/similar.py:40`); Related panel + catalog browse (`web/api/related.py:83-287`); Subsonic `getSimilarSongs(2)` / `getTopSongs` | Related artists inside its browse UI | none | One engine: the two don't share signals |
| 5 | **Artists you may like, gaps, new releases** | none (removed with the detach) | All three, background job, per-user hides / follows, release pings; taste = library + one library user's stars | none | **Per-user taste** (ratings, dislikes, plays of the signed-in account) |
| 6 | **Acquisition** | Request button only: server-to-server client (`integrations/music_grabber.py:63-108`), `grabber_requests` table (`:115-133`), routes (`web/api/grabber.py:45-155`), state by batch polling | One-click track / album, Spotify playlist sync, queue with states, inbox for ambiguous matches, `/api/v1` with scoped keys | none | Deep link "download this album" from BT's Related panel; request status without polling |
| 7 | **Subsonic / OpenSubsonic server** | ~55 methods (`web/subsonic/handlers.py:1026-1060`), API key + token auth, per-account scope (`web/subsonic/auth.py:76`), transcoding (`web/subsonic/transcode.py`) | client only (library index, playlist mirror) | full server | See ADR-003: `replayGain`, per-owner album / artist stars, album `setRating`, bookmarks, empty-list stubs, `transcodeOffset` |
| 8 | **One entry point / deployment** | One compose service, port 5000, `/healthz` (`web/api/media.py:155`), no `HEALTHCHECK` | One service, port 8099, `/healthz`, no `HEALTHCHECK`; three example bundles incl. a full stack | separate | Gateway / single origin; MG's UI assumes it is served at `/`; shared navigation |
| + | **Navidrome sync** | Playlists, stars, ratings, play counts, scrobbles, rescan (`integrations/periodic_sync.py:32`, `star_sync.py:40`, `rating_sync.py:52`, `play_sync.py:20`, `navidrome.py:60,187`); all inert without `NAVIDROME_URL` | One-way playlist mirror into ND; optional rescan hook | target | Mirror target is ND only |
| + | **Change detection** | watchdog / inotify, 10 s settle, 2 s drain (`scan/watcher.py:24,84`); one hook, `library_changed` (`hooks.py:9`) | Learns of library and analysis changes only by polling (index refresh each sync; a 60 s read-back loop) | own scanner | **A change feed**; a "scan this now" hint that does not need the admin password |
| + | **Notifications** | ntfy, scan events, batched (`notify/ntfy.py:13`) | ntfy, per-download and release pings | none | A digest; one topic convention |
| + | **Duplicates / trash, waveform, metadata editor, stats** | `trash.py`, `bpm/waveform.py`, `tagging.py` | none | none | nothing |

## Duplications

| What | BPM Tagger | Music Grabber | Drift today | Plan |
|---|---|---|---|---|
| User systems | `players` + row-less admin, cookie + CSRF | users + sessions, bearer | unrelated | ADR-002 |
| `text.py` normalizers | `bpm_tagger/text.py` | twin | identical, **pinned** by a golden file | keep pinned |
| `matching.py` | `score` + DB-backed `library_match` (`matching.py:67,100`) | same `score`, index-backed match | `score` identical, **not pinned** | add a pin test for `score` |
| LRCLIB fetcher | `integrations/lrclib.py` | twin | ~8 lines | pin, or MG stops fetching when BT owns lyrics (slice 7) |
| Lyrics / tag / cover writers | `bpm/lyrics.py`, `tagging.py` | twins | diverged | leave; each owns the tags it writes |
| `path_template.py`, `ratelimit.py` | present | twins | identical / ~6 lines | pin |
| Deezer public-catalog client | `integrations/deezer_catalog.py` | twin | diverged widely | leave; contract is the JSON API, not shared code |
| Suggestion engines | Related / similar (per-account weights) | For you (library-wide taste) | different inputs | slice 5: MG reads per-user taste from BT |
| Library index | `tracks` table + tag index | own cache table filled by filesystem scan or OpenSubsonic walk | by design (no shared DB) | keep; feed it from BT's change feed |
| Subsonic | server (`web/subsonic/`) + ND client (`integrations/navidrome.py:28`) | client | n/a | MG's client can target BT's server |
| ntfy | `notify/ntfy.py` | subset | diverged | leave; align topic / title conventions |
| Design systems | `frontend/src/styles/design-system.css` (dark oklch, `--bg --surface --text --accent`, ~1500 lines) | "liner notes" (warm paper / ink, `--paper --ink --signal`, ~200 lines) | **no shared token names**, despite what MG's CLAUDE.md says | ADR-004 |
| Health / activity feeds | `/healthz`, `/api/activity` | `/healthz`, `/api/activity` | different shapes | gateway health; shell shows both |
