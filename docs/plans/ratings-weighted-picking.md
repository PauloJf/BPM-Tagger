# Plan: 1–5 star ratings + rating-weighted picking

> Current status of all plans is tracked in [STATUS.md](STATUS.md).

Status: ✅ **Built** (2026-09-25) on `feat/ratings`, all six phases, **uncommitted / unreleased**.
See "Build notes" at the end for where the build deviated from this plan.
Code references are as of `75474a7` (branch `feat/subsonic-api`). Prefer the symbol
names over the line numbers.

## Goal

Replace the binary star / dislike controls with **per-account 1–5 star ratings**, keep
**dislike as a separate per-account hard exclusion**, and make ratings **weight the
probability** that a track is picked by Run, Listen shuffle and radio, "similar", and the
Subsonic API's random, similar and Run picks. Weighted sampling replaces today's
"starred first" sort. The cadence eligibility rule (the stretch limit) stays a hard filter.
An admin **Settings → Ratings & picking** section tunes the weights and a **new-songs**
multiplier.

## Why

Today's preference is a sort key, not a probability. `api_run_queue` sorts eligible
matches by `(not starred, -play_count, deviation)`, keeps the top `count` and then
shuffles only that slice (`web/api/run.py` → `_matches`, `picked[:count]`,
`random.shuffle`). When enough starred tracks match a target, **no unstarred track ever
enters the queue**. The same favourites repeat run after run, and a starred track can't
be ranked above another starred track. Dislike is the only other signal. It is
library-wide, so a player user's dislike removes a song for every account.

## Current state (verified 2026-09-25)

| Thing | Where | Notes |
|---|---|---|
| `tracks.starred`, `tracks.disliked` (0/1) | `db/base.py` tracks columns | Library-wide. Set by `POST /api/track/star` / `/dislike` (`web/api/tracks.py`, `login_required` → players too) and by Subsonic `star`/`unstar` |
| Run preference | `web/api/run.py` `_matches` | `run_prefer_starred` (default true), `run_prefer_familiar` (default false) in `config.py`; Settings toggles in `Settings.tsx` |
| Dislike exclusion | `db/tracks.py` `_RUN_FILTER` / `get_run_candidates*`, `web/similar.py` `take()`, `player.tsx` source-radio `pool.filter(!disliked)` | `/api/listen/queue` returns disliked tracks (flagged) for in-order play |
| Listen shuffle / radio | `frontend/src/lib/player.tsx` `shuffled()` (Fisher–Yates), source-radio and similar-radio effects | Client-side and uniform |
| Similar | `web/similar.py` `similar_tracks` | Artist pool uniformly shuffled; tempo neighbours in DB order |
| Star sync | `integrations/star_sync.py` | local/remote/base three-way merge on `starred` / `starred_base` |
| Play counts | `tracks.play_count` (global, Navidrome-merged via `play_sync.iter_all_songs`, bumped on scrobble); `play_events(owner, file_path, run_id, …)` per account | `iter_all_songs` (search3 walk) already returns each song's `userRating` |
| Accounts | `web/auth.py` `session_owner()` → `admin` / `player:<id>` / `guest`; Subsonic `who.owner` → `admin` / `player:<id>` | Same keys as `player_state`, `play_events` |
| "Added" date | none | `analyzed_at` is overwritten by re-analysis, so it isn't a stable "added" date |

## Decisions (agreed 2026-09-25)

| # | Question | Decision |
|---|---|---|
| D1 | Rating vs star | **`rating` is the source of truth** (NULL = unrated, 1–5). The star is **derived: starred ⇔ rating ≥ 4.** An incoming star on a < 4 track sets rating 4. An incoming unstar on a ≥ 4 track sets rating 3. |
| D2 | Migrating stars | `starred = 1` → **4★** (leaves 5★ free for real favourites) |
| D3 | Dislike | **Its own flag, independent of rating.** A hard exclusion whatever the rating. 1★ just means low weight. |
| D4 | Scope | **Per account.** Ratings and dislikes belong to `admin` / `player:<id>`. A player's dislike never affects another account. |
| D5 | Unrated fallback | **Own ratings only.** A player never inherits the admin's ratings or dislikes. |
| D6 | Guest (`RUN_PASSWORD`) | **Read-only.** No rating or dislike controls; picks with flat weights. |
| D7 | Weights | **Moderate** defaults (table below); **one global admin setting** applied to every account's own ratings |
| D8 | Old toggles | Retire `run_prefer_starred` → master **"Use ratings"** toggle (false carries over). Retire `run_prefer_familiar` → absorbed by the new-songs multiplier (true → **Less**). |
| D9 | "New" | **Unplayed and unrated by this account.** No core change. |
| D10 | New-songs knob | **Weight multiplier** on new tracks: Never 0 · Less 0.5 · **Neutral 1** · More 2 · Much more 4, or a custom value |
| D11 | Tempo closeness | No longer a preference. Everything inside the stretch limit is equal, consistent with v2.10's "stretch limit is the single authority". |
| D12 | Shuffle | **Weighted** for playlist / library / "mine" sources; **uniform** for album / artist |
| D13 | Where dislikes apply | Run, radio, similar and **every shuffle** skip your dislikes. **In-order play** of a playlist or album, or tapping the track, still plays it, shown with the dislike mark as today. |
| D14 | Where picking runs | **Server-side**, one shared sampler (`web/weighting.py`) used by Run, Listen, similar and Subsonic |
| D15 | Navidrome rating sync | **Two-way, opt-in, its own phase.** Admin's ratings only. |
| D16 | Star sync | **Kept**, working on the admin's derived star. It becomes **push-only for songs** while rating sync is on. |
| D17 | Subsonic | `setRating` + `userRating` + star/unstar through the caller's derived star + weighted `getRandomSongs` / `getSimilarSongs(2)` / "Run · preset" playlists that skip the caller's dislikes |
| D18 | UI | Shared **`<RatingStars>`** widget plus a separate **⊘ dislike** button everywhere the star toggle is today |
| D19 | Extras in scope | Stats rating-distribution card · rate tracks from a finished run in the Run journal · Tracks-page rating filter + sort |
| D20 | Rollout | **On by default with Moderate weights**, called out in the CHANGELOG as a behaviour change |
| D21 | File tags | **Out of scope** (per-account ratings don't fit one file tag, and tag writing belongs to the core) |

## Design

### Data model

New table (created in `db/base.py`, additive; queries in a new `db/ratings.py` mixin
composed in `database.py`):

```sql
CREATE TABLE IF NOT EXISTS track_ratings (
    owner       TEXT NOT NULL,              -- 'admin' | 'player:<id>'  (never 'guest')
    track_id    INTEGER NOT NULL,
    rating      INTEGER,                    -- NULL = unrated, 1..5
    disliked    INTEGER NOT NULL DEFAULT 0,
    rating_base INTEGER,                    -- admin only: Navidrome rating at last sync (D15)
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (owner, track_id),
    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
);
```

- The table is keyed by **`track_id`**, like `track_artists`, so it follows the row and cascades
  on a hard delete. A row with `rating IS NULL AND disliked = 0` is deleted rather than kept.
- **`tracks.starred` and `tracks.disliked` stay** as the **admin's projection**, written
  in the same transaction as every admin rating or dislike change. Everything that reads
  them library-wide keeps working unchanged: star sync, Suggestions seeding
  (`grabber/suggestions.py`), Cadence, `db/playlists.py`, and the Subsonic album-index
  `MAX(starred)`. `starred_base` is untouched.
- **Migration (one-shot, guarded by table creation):**
  `INSERT INTO track_ratings (owner, track_id, rating, disliked, updated_at)
   SELECT 'admin', id, CASE WHEN starred=1 THEN 4 END, COALESCE(disliked,0), now
   FROM tracks WHERE starred=1 OR disliked=1`. The existing pre-migration `.bak-<version>`
  copy (`db/base.py`) covers rollback.
- **Player deletion** (`db/players.py` `delete_player`) also deletes that owner's
  `track_ratings` rows.
- Index `play_events(owner, file_path)` for the per-account "unplayed" check.

**Behaviour change to call out:** today an admin dislike also hides the song from player
users. After migration it only hides it for the admin (D4, D5).

### "New" (D9)

Per account: **no rating and not played by this account.**

- **admin:** `COALESCE(tracks.play_count, 0) = 0`. This count is global and Navidrome-merged,
  so "unplayed" for the admin really means "nobody has played it anywhere". That's
  acceptable, since the admin is the library owner. Documented.
- **player:<id>:** no `play_events` row with that owner.
- **guest:** new-songs multiplier ignored (flat weights, D6).

### The weight function

```
weight(track, owner) =
    0                                     if disliked by owner      → hard-excluded, never sampled
    W[rating]                             if rated
    W[unrated] × new_factor               if unrated and unplayed by owner ("new")
    W[unrated]                            otherwise
```

Moderate defaults (D7), with unrated as the baseline:

| Level | 1★ | 2★ | 3★ | unrated | 4★ | 5★ | new ×|
|---|---|---|---|---|---|---|---|
| Weight | 0.1 | 0.5 | 1 | 1 | 3 | 6 | 1 (Neutral) |

- With "Use ratings" off, every non-disliked track weighs 1 and the new factor is ignored.
  That means a uniform draw, which is today's `prefer_starred=false` behaviour minus the
  closeness sort (D11).
- **Weight 0 (a level or "Never" new) means "only if nothing else is left".** Zero-weight
  tracks fill in after every positive-weight candidate, in random order. That mirrors
  the existing `recycled` fallback so a thin pool never starves a refill.

### Sampler — `bpm_tagger/web/weighting.py` (D14)

This is a pure module with no Flask and no DB access. It's an optional layer: only `web/` and
`web/subsonic/` import it, so `test_core_isolation` still holds.

- `weights_from_config(cfg) -> Weights`, which validates and clamps (0 ≤ w ≤ 100).
- `track_weight(row, weights, *, use_ratings, new_eligible) -> float`. `row` carries
  `rating`, `disliked` and `is_new`, already resolved for the owner by SQL.
- `sample(items, k, weight_fn, rng) -> list`: weighted sampling without replacement
  (Efraimidis–Spirakis: key = `u ** (1 / w)`, top-k), with zero-weight items appended
  after the others.
- `weighted_order(items, weight_fn, rng)`: the same keys over the whole list, i.e. a
  weighted permutation, used for shuffles.
- `rng` is injectable (`random.Random(seed)`) so tests are deterministic.

The DB side resolves the owner's view in SQL. `get_run_candidates*`, the Listen source
query, `subsonic_artist_tracks`, `subsonic_bpm_neighbours` and `get_random_songs` gain an
`owner` argument and
`LEFT JOIN track_ratings r ON r.track_id = t.id AND r.owner = ?`, returning `rating`,
`disliked` (the owner's, replacing the `tracks.disliked` filter) and `is_new`. The
hard-coded `tracks.disliked` filter in `_RUN_FILTER` becomes the owner's `r.disliked`.

### Where it applies

| Surface | Today | After |
|---|---|---|
| Run queue (`api_run_queue`) | sort starred → plays → deviation, top `count`, shuffle | `_eligible` (unchanged hard filter) → drop exclude → **`sample(k=count)`** → shuffle playback order. Playlist-first + library top-up structure unchanged: sample the playlist pool first, then top up from the library pool. |
| Listen source radio | client fetches pool, Fisher–Yates, filters disliked | client POSTs `exclude` to a new server pick (`POST /api/listen/pick` {source, count, exclude}) → weighted batch, owner's dislikes excluded, same recycle-when-exhausted rule |
| Listen shuffle (playlist / library / mine) | client `shuffled()` | `/api/listen/queue?order=weighted` returns the tracks in `weighted_order` minus the owner's dislikes. `playQueue` gets an option to take that order as-is (anchor track still pinned first). |
| Listen shuffle (album / artist) | client `shuffled()` | unchanged, uniform (D12), but skips your dislikes (D13) |
| In-order play | plays everything, dislike shown | unchanged (D13) |
| Similar (`similar.py`, `/api/related/library`, similar radio) | artist pool shuffled; tempo neighbours DB order | artist pool `weighted_order`. Tempo band over-fetched, then `sample`. Owner's dislikes skipped. |
| Subsonic `getRandomSongs` / `getSimilarSongs(2)` / "Run · preset" playlists | random / similar.py / starred first | weighted for `who.owner`, skipping that owner's dislikes (D17) |

The response field `starred` on queue and track payloads is kept (derived per owner) for
compatibility. `rating` and `disliked` are added.

### API

- `POST /api/track/rating` {path, rating: 1–5 | null}: the caller's own rating.
  `login_required`. **403 for guest.** Returns `{rating, starred}`.
- `POST /api/track/dislike` becomes per-owner (same shape). 403 for guest.
- `POST /api/track/star` is kept as a compat shim for one release: star → rating 4 if below 4,
  unstar → 3 if ≥ 4.
- `GET /api/track` and the list and queue endpoints return the caller's `rating` and `disliked`.
- `GET /api/settings/pick-preview` (admin) returns counts per level for the admin, and optionally
  for a chosen account, plus the **expected share of picks** per level under the current
  (unsaved) weights. The Settings section uses it as a live preview.
- `GET /api/runs/<id>/tracks` lists a finished run's tracks from `play_events.run_id`,
  with the caller's ratings, for the Run journal (D19). Owner-scoped like the journal.

### Config (`config.py`)

| Key | Env | Default |
|---|---|---|
| `pick_use_ratings` | `PICK_USE_RATINGS` | `true` |
| `pick_weights` | `PICK_WEIGHTS` (six comma-separated numbers, order `1,2,3,unrated,4,5`) | `0.1,0.5,1,1,3,6` |
| `pick_new_factor` | `PICK_NEW_FACTOR` | `1` |

Migrating the old toggles (D8):

- **settings.json:** on load, `run_prefer_starred: false` → `pick_use_ratings: false`, and
  `run_prefer_familiar: true` → `pick_new_factor: 0.5`, applied only when the new keys
  aren't already set. Then both old keys join `config._DEAD_SETTINGS` and are swept.
- **Env:** `RUN_PREFER_STARRED` / `RUN_PREFER_FAMILIAR` are honoured the same way as
  fallbacks for one release, with a deprecation warning in the log. After that they're
  removed and documented as such.

### UI

- **`<RatingStars>`** (`frontend/src/components/`): tap a star to set it, tap the current star to
  clear. Uses `role="radiogroup"` with arrow keys. It has a compact form: one star showing the
  number, which opens the widget. It replaces the star toggle in the player bar (compact on mobile),
  TrackDetail, the Run queue, Listen rows, Tracks rows and the queue drawer. **⊘ Dislike** is a
  separate button in the same places. Guests see neither.
- **Settings → Ratings & picking** (admin) is a new section. It holds the "Use ratings" toggle, six
  weight inputs (a number plus slider), a new-songs preset segmented control with a custom value,
  "Reset to defaults", and the live **expected-share preview** bar from `pick-preview`. The old
  Run "Prefer starred / familiar" toggles are removed from the Run section.
- **Tracks page:** the starred / disliked filters become **rated ≥ N / unrated / disliked**
  (the admin's view), and a **Rating** sort is added (`db/constants.py` `TRACK_SORTS`).
- **Stats:** a rating-distribution card (1–5, unrated, disliked) that follows the existing
  owner switch (`run_owners`), replacing the starred/disliked counters.
- **Run journal:** a finished-run row expands to its tracks, each with `<RatingStars>`.

### Navidrome rating sync (D15, D16) — opt-in

- New toggle `navidrome_sync_ratings` (env `NAVIDROME_SYNC_RATINGS`, default off). It only
  touches **admin** rows and runs as a job in `integrations/periodic_sync.py` plus a manual
  "Sync ratings now" button.
- **Pull** uses the existing `iter_all_songs` walk (`userRating` on each song), shared with
  the play-count pull so there's one walk when both are on. **Push** uses `setRating(id, 0–5)`
  (0 = clear), a new `navidrome.set_rating`.
- The merge is local / remote / `rating_base`, like `merge_star`: one side changed → take it;
  both changed differently → **local wins**; afterwards `rating_base` = the agreed value.
- **Star sync while rating sync is on** becomes push-only for songs: Navidrome's star just
  mirrors the admin's derived star. With rating sync off, star sync runs as today on the
  projection, with incoming star → 4 and incoming unstar → 3 (D1).

### Subsonic (D17)

- `setRating` (`id` = song; `rating` 0–5, 0 clears) writes `who.owner`'s rating. A non-song
  id returns error 0 ("ratings are for songs only"), since album and artist ratings are out of scope.
- `userRating` goes on every song child (`views.song`), for the caller's own rating.
- `star` / `unstar` on songs map to the caller's derived star (D1). Album and artist stars are
  unchanged (`subsonic_stars`).
- `starred` on song children and `getStarred(2)` songs are derived per owner (rating ≥ 4).
- Weighted `getRandomSongs`, `getSimilarSongs(2)` and the "Run · preset" playlists (see the
  table above).
- `docs/subsonic-api.md` is updated, along with its endpoint-reference sync test.

## Phases

Each phase is a mergeable commit set with its own tests. Phases 1–3 are the minimum
shippable release.

### Phase 1 — Per-account ratings (backend)

`track_ratings` + migration + `db/ratings.py`, keeping the admin projection in sync,
`/api/track/rating`, per-owner `/api/track/dislike`, the `/api/track/star` shim, per-owner
dislike in every candidate query, and cleanup on player deletion.

Tests (`tests/test_ratings.py`): migration (starred → 4, disliked kept, others absent),
projection sync, per-owner isolation (a player's dislike doesn't hide the song from admin or
another player), guest 403, cascade on track delete, player-delete cleanup.

### Phase 2 — Weighted picking (backend)

`web/weighting.py`, config keys + migration of the old toggles, `pick-preview`, Run queue,
Listen `pick` + `order=weighted`, and `similar.py`.

Tests: the sampler with a seeded RNG (determinism, zero-weight fallback, k > n);
a statistical test (10k draws, 4★ ≈ 3× unrated within tolerance); the Run cadence filter is
still hard (an out-of-limit 5★ is never queued); new-factor 0 → new tracks only fill in
last; settings migration (prefer_starred false → use_ratings false; familiar → 0.5); the old
keys are swept. Update the existing `test_api_*run*` tests that assert starred-first order.

### Phase 3 — UI

`<RatingStars>` + dislike button on every surface, the Settings section with preview, the
player's radio and shuffle moved to the server pick/order, the Tracks filter and sort, the
Stats card, and Run journal track rows.

Tests (vitest): RatingStars interaction and keyboard, guest hides controls, player radio
calls `/api/listen/pick` with `exclude`, Settings preview. Also `npm run typecheck`,
`npm run build`, and screenshots refreshed via `scripts/screenshots/`.

### Phase 4 — Subsonic

`setRating`, `userRating`, per-owner derived star, weighted random / similar / Run
playlists, docs + sync test.

### Phase 5 — Navidrome rating sync (opt-in)

`navidrome.set_rating`, a merge function + tests mirroring `test_star_sync.py`, the shared
search3 walk, the periodic job, star sync going push-only while it's on, and the Settings
toggle + button.

### Phase 6 — Docs + release

README + DOCKERHUB_README (the ratings feature, the Ratings & picking settings, the new env
vars, the removed/deprecated `RUN_PREFER_*`, and the dislike scope change), a CHANGELOG
entry flagging the **behaviour changes** (weighted instead of strict starred-first; admin
dislikes no longer hide songs from players), and STATUS.md. The version bump stays with the
release commit per CLAUDE.md.

## Core isolation

Nothing in `bpm/`, `scan/`, `main.py` or `config.py`'s core keys changes behaviour.
`track_ratings` is an additive table in `db/`, which already holds `starred` / `disliked`.
The sampler lives in `web/` and is imported only by `web/` and `web/subsonic/`. Ratings are
**not** written to file tags (D21), so tag writing is untouched. `test_core_isolation.py`
and the `core-only` CI job must stay green. New `db/ratings.py` tests that need no optional
deps get `pytestmark = pytest.mark.core`.

## Out of scope

- **Rating file tags** (POPM / FMPS_Rating / RATING). One tag can't hold per-account ratings,
  and tag writing is core. A possible later admin-only export.
- **Album / artist ratings** (Subsonic `setRating` on non-songs).
- **Per-account weight tuning.** Weights are global (D7).
- **An admin library-wide "hide for everyone"** exclusion. Considered and declined (D4).
- **Inheriting the admin's ratings** for new players (D5).
- **"Recently added" as a new-songs criterion.** It would need a core `tracks.first_seen_at`
  column. Revisit if "unplayed and unrated" proves too narrow.
- **Tempo-closeness weighting** inside the stretch limit (D11).
- **Snapshotting the rating at play time** in `play_events` (plays-by-rating stats).
- **Per-player Navidrome rating sync.** It would need per-player Navidrome credentials.
- **Weighting the Suggestions seeding** by rating. It keeps using the admin's derived star.

## Open questions

- **Listen radio with a small pool:** should the exclude window be the whole queue or the
  last N tracks? Today it's the whole queue, then recycle; keep that unless it feels
  repetitive in testing.
- **Weighted order on a very large library shuffle** (tens of thousands of tracks): the
  server-side `weighted_order` is O(n log n) and cheap, but returning every path could be
  heavy. We may need to page it or return the first N and let radio refill the rest.
  Measure in Phase 2.

## Build notes (2026-09-25)

Built in one pass: Phase 1, the sampler and the config by hand, then Phases 2–5 by parallel
agents against a fixed API contract, and the Phase 6 docs by hand. The deviations and
additions:

- **Shared seeding SQL.** `db/ratings.py::SEED_ADMIN_FROM_PROJECTION_SQL` is the one-time
  migration statement. Tests that seed `tracks.starred` / `tracks.disliked` with raw SQL run
  it afterwards to create the admin's rows the way a real upgrade does.
- **Player allowlist.** `api_track_rating`, `api_listen_pick`, `api_stats_ratings` and
  `api_run_tracks` are on it; each checks the caller's account itself.
  `/api/stats/ratings` returns `{owner, distribution}`. A player always gets its own counts
  and the guest gets an empty distribution.
- **Rating sync, unmatched songs.** A local track not matched in the Navidrome walk is
  treated as "remote unknown" and can only push, never pull. Otherwise a failed match would
  read as "cleared remotely" and wipe the local rating. There's a regression test.
- **Uniform shuffles.** The Library (Tracks page) shuffle and the Cadence page shuffle are
  arbitrary filtered views, not Listen sources, so they stay a plain shuffle. The Library
  shuffle does skip the admin's dislikes (D13). Weighted shuffle is wired for the Listen
  page and for PlaylistDetail.
- **Guest detection.** The client identifies the guest as `role === "player"` with
  `full_access` and no `username` (`lib/auth.tsx` `isGuest`).
- **Subsonic album lists.** `getAlbumList2 type=starred` and the album/artist `starred`
  timestamps stay the admin's library-wide view (the album index is global). This is
  documented in `docs/subsonic-api.md`.
- **Player bar.** The persistent PlayerBar never had a star toggle, so there was nothing to
  replace. Rating lives on the Run and Listen now-playing views, the queue, the track page,
  the Library and the journal.
