# Plan: Suggested Artists & Tracks page (grabber) + Related panels

> Current status of all plans is tracked in [STATUS.md](STATUS.md).

Status: Parts A–C shipped in v2.5.0; Part D ("queue similar" on player bar + Run page)
implemented in v2.5.1 (2026-07-14).

## Goal

Two features sharing one Deezer catalog client:

1. A new grabber page (`/suggestions`) that recommends **artists you don't have yet** and
   **tracks worth grabbing**, derived from what's already in the library (starred tracks
   weigh heaviest). Each suggested track has a one-click "Add to queue" that feeds the
   existing grab pipeline. Suggestions can be dismissed permanently and refreshed on
   demand.
2. A **Related panel** on the Artist, Album and Track detail pages: live "similar
   artists" and "similar tracks" lookups for the artist you're looking at, with links
   into the library for artists you already have and add-to-queue for the rest (§ "Part
   B" below).

## Data source decision (read this first)

**Do NOT use Spotify's recommendation endpoints.** `GET /recommendations`,
`GET /artists/{id}/related-artists`, audio-features etc. were deprecated by Spotify on
2024-11-27 for apps without extended quota access — they return 403 for this project's
development-mode app. Any plan built on them is dead on arrival.

**Use the Deezer public catalog API instead** — keyless, free, no OAuth, no cost:

| Endpoint | Purpose |
|---|---|
| `GET https://api.deezer.com/search/artist?q=<name>` | resolve artist name → Deezer artist id (+ picture) |
| `GET https://api.deezer.com/artist/{id}/related` | ~20 related artists, with images |
| `GET https://api.deezer.com/artist/{id}/top?limit=N` | artist top tracks (title, duration, album, cover, 30-s `preview` URL) |
| `GET https://api.deezer.com/artist/{id}/radio` | ~25 tracks "in the style of" an artist — the similar-tracks source for the Related panel |
| `GET https://api.deezer.com/track/{id}` | full track object incl. `isrc` (fetched lazily on enqueue only) |

Why this fits: zero configuration (works even when Spotify isn't connected), the grabber
already downloads from Deezer, the CSP already allows `https://*.dzcdn.net` images, and
track metadata maps 1:1 onto the existing `enqueue_grab()` meta dict. The codebase
already talks to this API keyless-ly: see `integrations/metadata.py` (`_deezer_get`) and
the image picker.

Rate limit: 50 requests / 5 s per IP. **Reuse the existing process-wide limiter** —
`from ..integrations.ratelimit import deezer_limiter` and call
`deezer_limiter.acquire()` before every request, exactly like `integrations/metadata.py`
does. Do not add a second throttle. A full suggestions refresh makes ~50 calls (see
algorithm); every call is best-effort (failures skip that seed, never abort the refresh).

Future sources (out of scope for v1, but keep the engine source-agnostic): Last.fm
`artist.getSimilar` (needs `LASTFM_API_KEY`), ListenBrainz Labs similar-artists.

## Architecture

```
bpm_tagger/integrations/deezer_catalog.py   NEW — pure HTTP client (pattern: metadata.py/musicbrainz.py)
bpm_tagger/grabber/suggestions.py           NEW — SuggestionsEngine (seeds → compute → store)
bpm_tagger/web/api/suggestions.py           NEW — suggestions_bp blueprint (also hosts /api/related/*)
bpm_tagger/db.py                            + 2 tables, + helper methods
bpm_tagger/grabber/sync_engine.py           + GrabberService owns a SuggestionsEngine
bpm_tagger/web/app.py                       + register suggestions_bp; + media-src CSP (only if previews built)
frontend/src/pages/Suggestions.tsx          NEW — the page
frontend/src/components/RelatedPanel.tsx    NEW — shared panel for Artist/Album/Track pages
frontend/src/App.tsx                        + route /suggestions
frontend/src/components/Nav.tsx             + "Suggestions" item in the Grabber section
frontend/src/pages/Artist.tsx               + <RelatedPanel artist={name} />
frontend/src/pages/Album.tsx                + <RelatedPanel artist={albumArtist} />
frontend/src/pages/TrackDetail.tsx          + <RelatedPanel artist={track.artist} />
frontend/src/lib/player.tsx                 + PlayerTrack.src/ephemeral (Part C previews)
tests/test_suggestions.py                   NEW
```

### 1. `integrations/deezer_catalog.py`

Model on [metadata.py](../../bpm_tagger/integrations/metadata.py) (which already does
keyless Deezer HTTP): module-level functions, `requests` with short timeouts,
`deezer_limiter.acquire()` before every call, log-and-return-empty on any failure.

```python
def search_artist(name: str) -> Optional[dict]        # {"dz_id", "name", "image_url"} best hit or None
def related_artists(dz_id: str) -> list[dict]          # [{"dz_id", "name", "image_url"}]
def artist_top_tracks(dz_id: str, limit=5) -> list[dict]
def artist_radio(dz_id: str, limit=25) -> list[dict]   # similar tracks, same shape as top tracks
    # track shape: {"dz_track_id","title","artist","album","duration_ms","cover_url","preview_url"}
    # NOTE: Deezer returns duration in SECONDS — multiply by 1000.
def track_isrc(dz_track_id: str) -> str                # "" on failure
```

`search_artist` picks the best hit by `normalize_artist` equality first, else the first
result (Deezer orders by relevance/fans).

### 2. `grabber/suggestions.py` — `SuggestionsEngine(config, db)`

Owned by `GrabberService` (`self.suggestions = SuggestionsEngine(config, db)` in
`GrabberService.__init__`). **No always-on thread** — a refresh spawns one daemon
`threading.Thread`, guarded by a `threading.Lock` so only one runs at a time. Public
surface:

```python
def refresh_async(self) -> bool      # False if already refreshing
@property refreshing: bool
last_error: str                      # surfaced in GET /api/suggestions
```

Constants in the module (no new env vars needed):
`SEED_LIMIT = 20`, `ARTIST_LIMIT = 24`, `TRACK_ARTISTS = 10`, `TRACKS_PER_ARTIST = 4`,
`TTL_DAYS = 7`.

#### Seed selection (pure DB, no network)

Fetch `artist, album_artist, starred` for all `status != 'deleted'` tracks and aggregate
in Python (libraries are tens of thousands of rows — trivial):

- Primary-artist name = `album_artist` if non-empty else the first segment of `artist`
  split on `,` / `&` / `feat.` (reuse `matching.extract_feat` + a split).
- `weight = track_count + 5 * starred_count`.
- Take the top `SEED_LIMIT` by weight. Starred artists therefore dominate; a library with
  no stars still works off track counts.

#### What "already in library" means for an ARTIST (read this — it's not binary)

An artist's library presence is a **track count**, not a flag. Build one map during
compute (and reuse the same helper for Part B):

```python
library_artists: dict[str, tuple[str, int]]
# normalize_artist(primary) → (display_name, track_count)
# counted over status != 'deleted' tracks, primaries from both artist and album_artist
```

Owning one track by an artist does NOT make them "had". Thresholds
(`OWNED_THRESHOLD = 3`, module constant):

- `track_count >= 3` → you *have* this artist: excluded from suggested artists.
- `track_count` 1–2 → you've *sampled* them: still eligible as a suggestion (arguably
  the best kind — you already liked one track), shown with a "You have 1 track" badge
  instead of being filtered out.
- Track-level filtering is unaffected: a suggested track is dropped only when
  `library_match()` resolves that specific recording, so suggesting a sampled artist
  never re-suggests the track you own.

#### Compute (network, inside the refresh thread)

1. Build `library_artists` (above) — used to score/filter suggestions.
2. Load dismissed keys from `suggestion_dismissed`.
3. For each seed: `search_artist(seed.name)` → `related_artists(dz_id)`. Skip seeds that
   don't resolve.
4. Aggregate related artists across seeds:
   `score(candidate) = Σ weight(seed that surfaced it)` (normalized weights). Drop any
   candidate that is dismissed or whose `library_artists` track count ≥ `OWNED_THRESHOLD`.
   Keep the top `ARTIST_LIMIT`; record per-candidate `seeds` (up to 3 seed names, for
   the "Because you like X" line) and `have_tracks` (0–2, for the sampled badge — store
   it in the `suggestions` row, e.g. reuse the `score`-adjacent columns or add
   `have_tracks INTEGER DEFAULT 0` to the table).
5. For the top `TRACK_ARTISTS` suggested artists: `artist_top_tracks(dz_id,
   TRACKS_PER_ARTIST)`. Drop tracks where `library_match(meta, db)` hits (build meta with
   `title/artist/album/duration_ms` + `norm_title/norm_artist`; no ISRC at this stage) or
   whose `dz_track_id` is dismissed.
6. Persist atomically: `db.replace_suggestions(artist_rows, track_rows)` (single
   transaction: `DELETE FROM suggestions` + inserts, stamping `computed_at`).

Call budget: ≤ 20 searches + ≤ 20 related + 10 top-tracks ≈ 50 calls ≈ 10–15 s with
throttling. That's why it runs on a thread and the UI polls.

#### Staleness / auto-refresh

`GET /api/suggestions` kicks `refresh_async()` when the table is empty **or**
`computed_at` is older than `TTL_DAYS`, and returns whatever is currently stored with
`refreshing: true`. Manual refresh button hits the explicit endpoint. No cron, no loop.

### 3. DB (`db.py`)

Two new tables in `_init_grabber_tables()` (or wherever the grabber `CREATE TABLE IF NOT
EXISTS` block lives — same additive style, safe on existing DBs):

```sql
CREATE TABLE IF NOT EXISTS suggestions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,            -- 'artist' | 'track'
    dz_id        TEXT NOT NULL,            -- deezer artist id or track id
    name         TEXT,                     -- artist name / track title
    artist       TEXT DEFAULT '',          -- tracks only
    album        TEXT DEFAULT '',
    duration_ms  INTEGER,
    image_url    TEXT DEFAULT '',
    preview_url  TEXT DEFAULT '',
    score        REAL DEFAULT 0,
    have_tracks  INTEGER DEFAULT 0,        -- artists only: library tracks you already own (0–2; ≥3 is filtered out)
    seeds        TEXT DEFAULT '[]',        -- JSON: seed artist names this came from
    computed_at  TEXT,
    queued_at    TEXT,                     -- set when user enqueues (survives until next refresh)
    UNIQUE(kind, dz_id)
);
CREATE TABLE IF NOT EXISTS suggestion_dismissed (
    kind         TEXT NOT NULL,            -- 'artist' | 'track'
    key          TEXT NOT NULL,            -- artist: normalize_artist(name); track: dz track id
    dismissed_at TEXT,
    PRIMARY KEY (kind, key)
);
```

Dismissal keys: artists by **normalized name** (stable across refreshes and sources),
tracks by Deezer track id (good enough; a re-suggested identical recording under a new id
is acceptable).

New `BPMDatabase` methods (follow existing style — `_connect()` context manager, dict
rows): `replace_suggestions(artists, tracks)`, `get_suggestions(kind)`,
`suggestions_computed_at()`, `mark_suggestion_queued(id)`,
`dismiss_suggestion(kind, key)` (also deletes matching `suggestions` rows),
`get_dismissed_suggestion_keys(kind)`.

### 4. API (`web/api/suggestions.py`, blueprint `suggestions_bp`)

Copy the structure of [queue.py](../../bpm_tagger/web/api/queue.py): `login_required`
on everything, `_check_csrf()` on POSTs, `_grabber()` helper returning
`state().tagger.grabber`, `409 grabber_disabled` when absent. Register the blueprint in
`web/app.py`'s loop.

| Route | Method | Behavior |
|---|---|---|
| `/api/suggestions` | GET | `{enabled, artists, tracks, refreshing, last_error, computed_at, seed_count}`. Kicks auto-refresh if stale (see §2). Each track gets live flags: `in_library` (`library_match`), `queued` (`queued_at` set, or matching non-terminal queue row). |
| `/api/suggestions/refresh` | POST | `refresh_async()`; 200 `{ok:true}` or 409 `{error:"already_refreshing"}`. |
| `/api/suggestions/dismiss` | POST | body `{kind, key}` → persist + prune; `{ok:true}`. |
| `/api/suggestions/artists/<dz_id>/tracks` | GET | Live `artist_top_tracks()` for one suggested artist (lazy expansion), each flagged `in_library`/`queued`. Not persisted. |
| `/api/suggestions/queue` | POST | Enqueue one suggested track — see below. Body: `{dz_track_id, title, artist, album, duration_ms, cover_url, suggestion_id?}`. |

#### Enqueue path (the subtle part)

`enqueue_grab()` dedupes on `spotify_track_id`, which Deezer tracks don't have. So the
queue endpoint does, server-side:

1. If the Spotify client `is_connected()`: `client.search_tracks(f"{artist} {title}",
   limit=5)`, score each against the Deezer meta with `matching.score()`, and if best ≥
   0.9 adopt its `spotify_track_id` + `isrc` + `album_artist`/`track_no`/`year`. This
   gives full dedupe and better provider matching for free.
2. Else (or no confident hit): fetch ISRC via `deezer_catalog.track_isrc(dz_track_id)`.
3. `db.enqueue_grab(meta)` (same meta dict shape as `queue.py:enqueue_manual`), then
   `g.request_sync()` and `db.mark_suggestion_queued(suggestion_id)` when provided.
4. Both external lookups are best-effort — never fail the enqueue because of them.

### 5. Frontend

**`pages/Suggestions.tsx`**, route `/suggestions` in `App.tsx`, nav item in the
**Grabber** section of `Nav.tsx` between "Add Music" and "Queue" (sparkles-style stroke
icon matching the 15 px `ic` set). Page is only reachable when the grabber is enabled —
same guard as [Search.tsx](../../frontend/src/pages/Search.tsx) ("The grabber is
disabled." card).

Use the established patterns: `useTitle`, `useGrabberStatus`, `api.get`/`api.post` with
react-query, `qc.invalidateQueries` after mutations, existing CSS classes (`card`,
`tracks-table`, `pl-track-row`, `chip chip--have`, `chip--queued`, `btn btn-soft btn-sm`).

Layout:

1. **Header** — title "Suggestions", subtitle "Based on your top and starred artists",
   a Refresh button (disabled + spinner text while `refreshing`), and "Updated <relative
   time>" from `computed_at`. Poll GET every ~3 s while `refreshing` (react-query
   `refetchInterval`).
2. **Suggested artists** — responsive card grid (CSS grid, `minmax(140px, 1fr)`), each
   card: artwork (reuse `Artwork`-style square with `image_url`), artist name, muted
   "Because you like {seeds[0]}" line (or "You have {have_tracks} track(s)" when
   `have_tracks > 0` — sampled artists, see the OWNED_THRESHOLD rule), and two actions:
   **Top tracks** (expands an inline lazy-loaded track list beneath the grid, one artist
   expanded at a time) and a small **×** dismiss.
3. **Suggested tracks** — rows identical to Search results: title/artist·album, right
   side `✓ in library` / `↓ queued` chips or "Add to queue" button, plus a bare **×**
   dismiss per row.
4. **Empty states** — never computed: "No suggestions yet" + Refresh CTA; empty library:
   explain seeds come from the library; `last_error` non-empty: warning flash.

Dismiss mutations optimistically remove the row and invalidate `["suggestions"]`.

## Part B: Related panel on Artist / Album / Track pages

Live, on-demand lookups (nothing persisted, no dismissals) reusing `deezer_catalog`.
Where the Suggestions page answers "what should I grab next?", the Related panel answers
"what's similar to what I'm looking at right now?".

### Endpoints (same `suggestions_bp` blueprint)

| Route | Method | Behavior |
|---|---|---|
| `/api/related/artists?name=<artist>` | GET | `search_artist(name)` → `related_artists()`. Each entry: `{name, image_url, track_count, library_name?}`. `track_count` = how many library tracks have this artist as primary (0 = not in library), from the same `library_artists` map as Part A; when > 0, `library_name` carries the library's display spelling so the UI can link to `/artist?name=...`. No binary `in_library` — the count is the truth. |
| `/api/related/tracks?name=<artist>` | GET | `search_artist(name)` → `artist_radio()`. Each track flagged `in_library` (via `library_match`, and when true include `file_path` so the UI can link/play) and `queued` (non-terminal queue row). |

Gating decision: these are **`login_required` only, NOT grabber-gated** — they're pure
read-only Deezer lookups, and the panel is useful for navigating your own library even
with the grabber off (in-library related artists become links). Only the add-to-queue
action (existing `/api/suggestions/queue`, Part A §4) stays grabber-gated; the frontend
hides Add buttons when `useGrabberStatus` reports disabled.

Server-side cache: a tiny in-memory TTL cache inside the API module — dict keyed by
`normalize_artist(name)` → `(expires_at, payload)`, TTL 24 h, cap ~200 entries (evict
oldest). Navigating between artist pages must not re-hit Deezer for artists seen this
process lifetime. Note: `in_library`/`queued` flags are computed per-request on top of
the cached Deezer payload, never cached themselves (the library changes).

Empty results (artist not found on Deezer, network down) return `{artists: []}` /
`{tracks: []}` with 200 — the panel renders a quiet "nothing found" line, never an error
flash.

### `frontend/src/components/RelatedPanel.tsx`

One shared component, prop `artist: string` (plus optional `context: "artist" | "album"
| "track"` if styling needs it). Placement:

- [Artist.tsx](../../frontend/src/pages/Artist.tsx): below the album list, seeded with
  `name`.
- Album.tsx: below the track list, seeded with `album_artist` (fall back to the first
  track's artist).
- [TrackDetail.tsx](../../frontend/src/pages/TrackDetail.tsx): near the bottom (below
  metadata), seeded with the track's artist.

Behavior:

- **Collapsed by default, lazy fetch on first expand** — a `section-label`-style header
  row ("Related · powered by Deezer") with a chevron toggle. No Deezer traffic from
  merely opening a detail page. react-query `staleTime: Infinity` per session (server
  cache handles cross-session).
- Two tabs or stacked sub-sections once expanded: **Similar artists** and **Similar
  tracks**. Artist cards render by `track_count`:
  - `0` — no badge; grabber on → a small "+" that expands that artist's top tracks via
    the Part A lazy endpoint `/api/suggestions/artists/<dz_id>/tracks`.
  - `1–2` — muted "1 track" / "2 tracks" badge linking to `/artist?name={library_name}`,
    AND the "+" top-tracks expansion (you sampled them; grab more).
  - `≥3` — `✓ n tracks` badge linking to the library artist page; expansion still
    available (per-track flags mark what you own).
  Track rows use the Search-page style: title, artist·album, then `✓ in library` chip
  linking to `/track?path=...` / `↓ queued` chip / "Add to queue" button when grabber is
  enabled.
- Reuse the add-to-queue mutation from the Suggestions page — extract it into a small
  hook (`hooks/useSuggestionQueue.ts`) both pages share, invalidating `["queue"]` and
  the panel's own query.

### Part B tests

- `/api/related/artists`: mocked catalog → `track_count` computed from normalized
  primaries and `library_name` returns the library display spelling; artist with 1
  track → `track_count: 1` (not excluded, not treated as owned); unknown artist →
  `{artists: []}` 200.
- `/api/related/tracks`: `library_match` hit → `in_library` + `file_path`; queue row →
  `queued`.
- Cache: second call with the same normalized name doesn't re-invoke the (mocked)
  catalog; flags still recomputed (star a matching track between calls → flag changes).
- Endpoints reachable with grabber disabled (no 409).

## Part C: 30-second track previews (listen before you grab)

Every Deezer track object already includes `preview` — a 30 s, 128 kbps MP3 URL on
`*.dzcdn.net`, keyless. The planned catalog shapes and the `suggestions.preview_url`
column already carry it. This part wires it to a play button on every suggested/related
track row.

### Playback: extend the existing ducking preview — do NOT add a second `<audio>`

[player.tsx](../../frontend/src/lib/player.tsx) already implements the exact semantics a
30 s clip needs: `preview(track)` fades the playing queue down, plays a one-off track,
and `onEnded` → `endPreview()` fades the queue back in at its saved position. The only
limitation is that the load path always builds the source from a library path
(`audioUrl(current.path)`).

Minimal change to `PlayerTrack` + the two places that set `a.src`:

```ts
export interface PlayerTrack {
  path: string;            // for previews: a synthetic key, e.g. "preview:dz:123"
  title: string;
  artist?: string;
  bpm?: number | null;
  src?: string;            // NEW: absolute stream URL; when set, used instead of audioUrl(path)
  ephemeral?: boolean;     // NEW: never persist (external clips die on reload anyway)
}
```

- Load effect and `goToPos`: `a.src = track.src ?? audioUrl(track.path)`.
- `persist()`: skip when the *queue* contains ephemeral tracks (only happens in the
  nothing-was-playing case below — filter them out rather than skipping wholesale).
- Everything else already behaves correctly with no changes:
  - identity/`isCurrent` works off the synthetic `path`;
  - no `bpm` → tempo-lock rate 1;
  - Media Session shows the clip's title/artist (cover 404 is explicitly tolerated);
  - clip ends → `onEnded` → queue resumes exactly where it was ducked;
  - `onError`'s HEAD probe against a cross-origin URL fails closed into the generic
    "playback failed" message — acceptable.
- Edge case to handle: `preview()` with nothing playing falls through to `play(track)`,
  which makes the clip a persisted single-item queue. The `ephemeral` flag exists for
  this — exclude such tracks in `persist()` so a reload never tries to resurrect a dead
  preview URL.

### UI

A small `PreviewButton` (in the shared `trackBits`/related component file), rendered on
any track row whose data has a non-empty `preview_url`: Suggestions-page track rows,
expanded artist top-tracks (both pages), and RelatedPanel similar-track rows.

```ts
const pt = { path: `preview:dz:${t.dz_track_id}`, title: t.title, artist: t.artist,
             src: t.preview_url, ephemeral: true };
playing-this-clip ? player.toggle() : player.preview(pt);
```

Icon: ▶ that becomes ■/pause while `player.isCurrent(syntheticPath) && player.playing`.
Because the clip runs through the normal player, the PlayerBar shows it and offers
stop/pause for free; starting a preview while music plays ducks and auto-resumes — no
extra state to manage.

### CSP

`media-src 'self'` must become
`media-src 'self' https://*.dzcdn.net;` in [app.py](../../bpm_tagger/web/app.py)
(preview clips stream from `cdns-preview-*.dzcdn.net` hosts). `img-src` already allows
`*.dzcdn.net` — no other CSP change.

### Caveats (note in code comments)

- Deezer preview URLs are not guaranteed immortal; `suggestions` rows live up to
  `TTL_DAYS`, so a stored URL can occasionally go stale. The player's error state covers
  it ("playback failed"); optionally re-fetch a fresh URL via `GET /track/{dz_track_id}`
  on error — nice-to-have, not v1.
- Spotify search results (the existing Add Music page) get NO preview button: Spotify
  removed `preview_url` for post-2024 apps. A Deezer ISRC cross-lookup could add one
  later — out of scope.

### Part C tests

Frontend has no test runner — verification is `npm run typecheck` plus manual: start a
library track, hit preview on a suggested track (duck + auto-resume), preview with
nothing playing (plays, doesn't persist across reload), preview URL 404 (error surfaces,
queue restorable).

### 6. CSP (Part A/B)

Artist/cover images come from `*.dzcdn.net` — **already allowed** in `img-src`
([app.py:139](../../bpm_tagger/web/app.py)). The only CSP change in this whole plan is
Part C's `media-src` addition above; skip it if Part C is deferred.

## Tests (`tests/test_suggestions.py`)

Follow existing conventions in `tests/conftest.py` (temp DB, Flask test client — check
how `test_*` files for queue/inbox authenticate and reuse that).

- **Seed selection**: seed weighting prefers starred artists; album_artist fallback;
  multi-artist strings reduced to primaries.
- **Compute** (monkeypatch `deezer_catalog.*` — no network in tests): related artists
  aggregate across seeds and are scored; artists with ≥ `OWNED_THRESHOLD` library tracks
  are excluded while an artist with 1 track survives with `have_tracks = 1`; dismissed
  artists/tracks excluded; tracks matching the library via `library_match` excluded;
  results persisted and replace prior rows.
- **DB**: migration creates tables on an existing DB; dismiss removes and persists across
  a recompute; `mark_suggestion_queued`.
- **API**: GET shape + `queued`/`in_library` flags; refresh returns 409 while running
  (patch the engine's lock/flag); dismiss requires CSRF; queue endpoint adopts Spotify
  meta when the mocked search returns a ≥0.9 match, falls back to Deezer ISRC otherwise,
  and dedupes (second enqueue → 409-style `already queued`).
- **deezer_catalog**: seconds→ms conversion; failure → empty results, no raise
  (monkeypatch `requests.get`).

Gates before commit: `pytest -q`, `ruff check bpm_tagger/ tests/`, `cd frontend && npm
run typecheck && npm run build`.

## Part D: "Queue similar" from the Run page and the player bar

Added 2026-07-14. Surfaces the related-tracks lookup where you're *listening*, not just
where you're browsing: a button on the **Run page** and on the global **player bar** that,
for the now-playing track, pulls similar tracks and lets you enqueue them without leaving
the player.

Reuses Part B's endpoint as-is: `/api/related/tracks?name=<artist>` already returns Deezer
radio tracks flagged `in_library` (+ `file_path`) and `queued`. Seed it with the
now-playing track's `artist` (from `player.tsx` state). No new backend endpoint required;
the same 24 h server cache and `deezer_limiter` apply.

### Queue routing (DECIDED 2026-07-14)

"Queue" means two different things in this app; Part D uses both, contextually:

- In-library related tracks (`in_library: true`, has `file_path`) → **append to the play
  queue** (Run mode's cadence-driven queue / the ordinary play queue, built in `Run.tsx`
  / `lib/player.tsx`). This is the primary action from the player: "keep this vibe going."
- Missing related tracks → the existing grabber-gated **Add to grab queue** action
  (`grab_queue`; reuse `hooks/useSuggestionQueue.ts`), shown only when the grabber is
  enabled.

### Cadence constraint (Run page only)

Run mode's whole premise is that every queued track folds to your target cadence. Related
in-library tracks appended to the **Run** queue must go through the same octave-fold /
target-BPM filter the normal run-queue builder uses (`Run.tsx` / `lib/player.tsx`), or they
break the run. Drop (or fold) tracks whose BPM can't reach the active target. On the
**general player bar** outside Run mode, no cadence constraint — append to the ordinary play
queue as-is.

### Placement

- [PlayerBar.tsx](../../frontend/src/components/PlayerBar.tsx): a small "≈ / queue similar"
  control on the now-playing track. Opens a compact popover of related tracks (same row
  style as `RelatedPanel`) with per-row enqueue, plus a "queue all in-library" shortcut.
- [Run.tsx](../../frontend/src/pages/Run.tsx): a "queue similar" affordance near the queue
  UI, seeded from the current track; enqueues are cadence-filtered per above.
- Reuse `RelatedPanel`'s row rendering and the Part B fetch (react-query, `staleTime:
  Infinity`); factor the shared bits out rather than duplicating.

### Caveat

`artist_radio` is per-*artist*, not per-track — "similar to this artist," not "similar to
this exact song." Good enough for v1; a track-level source (if one appears) could sharpen
it later. Note this in the UI copy ("Similar · powered by Deezer").

### Part D tests

- Player-bar enqueue of an in-library related track lands it in the play queue (not the
  grab queue); missing track routes to `useSuggestionQueue` (grabber path) and is hidden
  when the grabber is off.
- Run-page enqueue drops/folds a related track whose BPM can't reach the active target
  cadence; a foldable one is appended at the correct octave.

## Docs (required by project rules)

Same commit (or immediate follow-up): update **README.md** (Suggestions page under the
Grabber feature docs, Related panel under the library/UI docs, screenshots in
`docs/screenshots/`), **DOCKERHUB_README.md**, and **CHANGELOG.md** (v2.5.0 entry). Note
explicitly that suggestions and related lookups use Deezer's public API (no account/key
needed) and work even without Spotify connected.

## Implementation order

1. `integrations/deezer_catalog.py` + its tests.
2. `db.py` tables + helpers + tests.
3. `grabber/suggestions.py` engine + wiring into `GrabberService` + tests.
4. `web/api/suggestions.py` (Part A endpoints + Part B `/api/related/*`) + blueprint
   registration + tests.
5. `frontend` Part A: Suggestions page, route, nav item.
6. `frontend` Part B: `RelatedPanel` + `useSuggestionQueue` hook, mounted on Artist /
   Album / TrackDetail. `npm run typecheck && npm run build`.
7. Part C: `PlayerTrack.src`/`ephemeral` in player.tsx, `PreviewButton`, `media-src`
   CSP addition.
8. Manual verification: run locally (`run.ps1` / `npm run dev`) — refresh, dismiss,
   enqueue one track end-to-end into the queue page; open an artist page, expand the
   Related panel, follow an in-library link, queue a missing track; preview a suggested
   track while a library track is playing (duck + auto-resume).
9. Part D: "queue similar" on `PlayerBar.tsx` + `Run.tsx`, reusing `/api/related/tracks`;
   in-library → play queue (cadence-folded on the Run page), missing → grab queue.
   `npm run typecheck && npm run build`.
10. README / DOCKERHUB_README / CHANGELOG.

Parts A, B, C and D are independently shippable after step 4 — if splitting into
commits/PRs: Suggestions page first, then Related panels, then previews, then Part D
(depends on B's endpoint).

## Acceptance criteria

- Page hidden / API 409 when `GRABBER_ENABLED=false`.
- Works with Spotify disconnected (Deezer needs no auth); Spotify only enriches enqueues.
- Refresh never blocks a web request (thread + polling), never crashes on network
  failure (`last_error` surfaced instead), and respects Deezer throttling.
- No suggested artist you own ≥ 3 tracks of; artists with 1–2 tracks may be suggested
  and are badged with what you have; no suggested track that `library_match` resolves;
  dismissals survive refreshes and restarts.
- "Add to queue" lands a `pending` `grab_queue` item that the existing worker downloads
  without any special-casing.
- Existing DBs migrate cleanly (additive only).
- Related panel: no Deezer call until expanded; repeat visits to the same artist hit the
  server cache; in-library artists link to their library page; works (read-only) with
  the grabber disabled; all Deezer traffic goes through `deezer_limiter`.
- Part D: "queue similar" on the player bar enqueues an in-library related track into the
  play queue and routes a missing one to the grab queue (hidden when the grabber is off);
  on the Run page, enqueued related tracks respect the active target cadence (fold or drop).
- Previews: ▶ on any row with a `preview_url`; starting one while music plays ducks the
  queue and auto-resumes after the 30 s clip; clips never persist into the saved queue;
  no regression to library playback, tempo lock, or iOS lock-screen behavior (the
  `src ?? audioUrl(path)` change is the only touch on the load path).

## Out of scope (future ideas)

- Last.fm / ListenBrainz as additional similarity sources (engine is source-agnostic).
- Re-fetching a stale preview URL on playback error (Part C ships without it).
- Preview buttons on Spotify search results via Deezer ISRC cross-lookup.
- "Grab all top tracks of this artist" bulk action.
- ntfy digest ("12 new suggestions this week").
