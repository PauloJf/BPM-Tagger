# Plan: Multi-source Playlists (Spotify + Navidrome + Local) & Run-mode integration

> Current status of all plans is tracked in [STATUS.md](STATUS.md).

Status: **Phases 1–3 implemented** (2026-07-17). Phase 1: schema generalization,
diff/upsert sync, legacy migration. Phase 2: Navidrome source (Subsonic
`getPlaylists`/`getPlaylist`, metadata-matched coverage, source-aware add/sync API,
front-end source picker + new/removed badges). Phase 3: run-queue playlist scope
(`playlist=` param), player-readable `/api/run/playlists` + allowlist, Run-page
source picker. Phases 4–5 proposed.
Supersedes the earlier "native playlist object" draft: we **reuse and generalize the
existing Playlists section** instead of building a parallel system.

## Core idea

The existing Playlists section already has everything a run-playlist needs:
per-track coverage (`match_status` = have/missing/unknown + `matched_file_path`),
"queue the missing ones in the grabber," sync, and m3u export. It's just
**hardcoded to Spotify**. Generalize its *source* to `spotify | navidrome | local`
— every other behavior (coverage, grabber-queue-missing, export) applies unchanged —
then let Run mode use any playlist as its candidate pool.

Per playlist track, the state is simply: **we have it and can play it**, or **we don't
(missing / failed)** and can offer to queue it in the grabber.

## Locked decisions

- **Reuse & generalize the existing `playlists` / `playlist_tracks` tables** (add a
  `source`), rather than a new native table.
- **Build order:** Spotify (exists) + **Navidrome first**; **Local** source ships
  **later, together with "Add to playlist"** (a Local playlist is empty/useless
  without an authoring action — see Deferred).
- **Interim access:** until a per-user association panel exists, **all playlists are
  shared to the single Run-only player** as run sources.
- **Sync:** manual **Sync button**; sync **diffs** rather than wipes, so we can show
  **new tracks** (store first-seen/synced dates, badge new rows).
- **Deferring the users panel also defers the riskiest Navidrome unknown:** the admin
  adds Navidrome playlists with the admin's **own** Subsonic creds, so `getPlaylists`
  returns the admin's own + public playlists — no "read another user's playlist by
  username under multi-library" problem until per-user association actually lands.

## The hard rule (unchanged)

Run mode only uses a track it can **stream locally at a known BPM** (analyzed, not
disliked, not deleted). Everything else in a playlist is shown as **missing**, never
silently dropped, and can be queued to the grabber.

---

## Schema changes (`db.py::_migrate`, additive + one guarded rebuild)

### `playlists`

Today `spotify_id TEXT UNIQUE NOT NULL` ([db.py:148](../../bpm_tagger/db.py)) hardcodes
Spotify. Changes:

- `ADD COLUMN source TEXT NOT NULL DEFAULT 'spotify'` — `spotify | navidrome | local`.
- `ADD COLUMN navidrome_id TEXT` — Subsonic playlist id (source=navidrome).
- **Relax `spotify_id`** so Navidrome/Local rows can leave it null. SQLite **cannot
  drop `NOT NULL`/`UNIQUE` in place**, so this is a one-time **table rebuild**
  (create new schema → copy rows with `source='spotify'` → drop → rename) guarded by a
  migration flag. This is the single riskiest migration (grabber-shared table) → needs
  a test against existing grabber data.

### `playlist_tracks`

- `ADD COLUMN source_track_id TEXT` — stable per-source id (Spotify track id / Navidrome
  song id) used as the diff key. (`spotify_track_id` stays for grabber back-compat.)
- `ADD COLUMN first_seen_at TEXT` and `ADD COLUMN is_new INTEGER DEFAULT 0` — for the
  "new tracks since last sync" badge. (`added_at` already exists.)
- `ADD COLUMN removed_at TEXT` — NULL = present in source; set = **tombstone** (was in
  the playlist, now gone from the source). See membership vs. match, below.

### Two independent states per track (do not conflate)

| Axis | Meaning | Values |
|---|---|---|
| `match_status` | Do *we* have the local file? | `have` / `missing` |
| membership (`is_new` / `removed_at`) | Is it in the *source* playlist now? | `present` / `new` / `removed` |

A track can be `have` + `removed` or `missing` + `new`. **"Removed" ≠ "missing"** — the
UI needs distinct badges.

### Sync becomes a diff, not a replace

`replace_playlist_tracks` currently **deletes every row and re-inserts**
([db.py:1140](../../bpm_tagger/db.py)) — that destroys `added_at` and any new/removed
state. Replace with an **upsert keyed by `(playlist_id, source_track_id)`**:

- **in source, not in DB** → insert, stamp `first_seen_at`, `is_new=1`.
- **in both** → update metadata / `match_status` / `matched_file_path`, keep dates. If
  the row had `removed_at` set → it was **re-added**: clear `removed_at`, re-flag
  `is_new`.
- **in DB, not in source** → set `removed_at = now` (**tombstone**; keep the row +
  metadata so the UI can still show title/artist). Do **not** delete.
- **Tombstone lifecycle:** each sync first **clears prior tombstones** (rows with
  `removed_at` set), then recomputes — so the detail view always shows "what changed
  since your last sync." `is_new` clears when the detail view is opened.
- stamp `playlists.last_synced_at` each sync.

**Runnable set excludes tombstones** — the Run-mode query adds `removed_at IS NULL`, so
a removed track never re-enters a run, but stays visible in the detail view (dimmed /
struck-through, in a "Removed" group with the date).

Notes: **Local** playlists don't sync → removals there are explicit user deletes
(hard-delete; tombstones are a sync-only concept). A removed track that was already
**queued in the grabber** keeps its grab (queued deliberately) — surface that in the UI.

---

## Decouple the Playlists section from the grabber

Add/sync currently hard-fail unless the grabber is enabled and Spotify connected
([playlists.py:59](../../bpm_tagger/web/api/playlists.py)). New rule:

- **Listing, Navidrome, Local** work with the **grabber off**.
- **Spotify add/sync** still needs the grabber + Spotify connection.
- **"Queue missing in grabber"** (any source) needs the grabber → hide the button when
  it's off.

Note: queuing a *missing Navidrome* track re-downloads it from the grabber's providers
(Deezer/yt-dlp) by metadata — it does **not** copy from Navidrome. Fine, just not a
Navidrome pull.

---

## Navidrome source

New Subsonic client functions in `integrations/navidrome.py` (same `_sub_params`
pattern):

- `get_playlists(url, user, pwd)` → `getPlaylists` (admin's own + public).
- `get_playlist(url, user, pwd, id)` → `getPlaylist`, entries carry `path`.

Sync flow: `getPlaylist` → for each song, resolve to a local file via
`best_match_id` / `_paths_match` ([navidrome.py:82](../../bpm_tagger/integrations/navidrome.py))
→ set `matched_file_path` + `match_status` (`have`/`missing`). Missing rows are
grabber-queueable like Spotify's.

**Add-Navidrome UX:** browse-and-pick from the admin's Navidrome playlists
(`GET /api/navidrome/playlists`, admin), not paste-an-id.

---

## API

Generalize existing endpoints to be source-aware; add run-facing reads.

| Endpoint | Change |
|---|---|
| `POST /api/playlists` | Accept `source`; Spotify (URL) as today; Navidrome (pick from list) → create + sync |
| `POST /api/playlists/<id>/sync` | Source-aware: Spotify via grabber, Navidrome via Subsonic; both diff-upsert |
| `GET /api/playlists`, `.../tracks`, `.../export.m3u` | Unchanged shape; now multi-source, `source` + new-track flags surfaced |
| `GET /api/navidrome/playlists` | **New** — admin's importable Navidrome playlists |
| `GET /api/run/playlists` | **New** — player-readable list of playlists usable as run sources |
| `GET/POST /api/run/queue` | **New param `playlist=<id>`** — candidate pool = that playlist's matched/BPM/non-disliked tracks; fold + tolerance + starred + shuffle + `exclude` refill unchanged ([run.py:35](../../bpm_tagger/web/api/run.py)) |

Add `api_run.*` playlist reads to `_PLAYER_ALLOWED`
([app.py:63](../../bpm_tagger/web/app.py)); playlist **management** (add/sync/delete)
stays admin-only.

---

## Frontend

- **Playlists page** ([Playlists.tsx]): source **icon/badge** per playlist (Spotify /
  Navidrome / Local); "Add playlist" offers Spotify URL **or** Navidrome pick.
- **PlaylistDetail** ([PlaylistDetail.tsx]): **new-track icon** on `is_new` rows; a
  dimmed/struck-through **"Removed" group** for `removed_at` tombstones (distinct from
  the `missing` badge); last-synced date; **Sync** button; **Queue missing** button
  (grabber-gated).
- **Run page** ([Run.tsx]): playlist picker as a run source alongside Whole
  library / Starred; coverage hint ("N of M available").

---

## Phasing

| Phase | Scope |
|---|---|
| **1** ✅ | Schema generalization (rebuild + `source`), diff/upsert sync, membership tracking (new/removed tombstones). Spotify behavior preserved; 352 tests + ruff green. |
| **2** ✅ | **Navidrome source**: Subsonic `getPlaylists`/`getPlaylist`, add-by-pick, sync+resolve+coverage, new/removed tracking UI. Metadata matching (not path) to sidestep multi-root fragility. Missing-track grabber-queue stays Spotify-only (grab_queue is keyed on spotify_track_id). |
| **3** ✅ | **Run-mode integration**: `playlist=<id>` on `/api/run/queue` (pool = matched, BPM-tagged, non-disliked, non-tombstone tracks); `GET /api/run/playlists` (in `_PLAYER_ALLOWED`) with per-playlist available counts; Run-page source picker. All playlists shared to the player. |
| **4** *(deferred)* | **Local source + "Add to playlist"** (from track pages and the Run player) — shipped together, since Local is inert without authoring. |
| **5** *(deferred)* | **Per-user association**: admin users panel mapping Navidrome users → playlists; Navidrome player login (`nd_username` session); retire-or-keep `RUN_PASSWORD`; periodic sync; "play everything, force tempo" toggle. |

Recommended first build: **Phase 1** — it's pure backend groundwork that keeps Spotify
working while unlocking every other source.

---

## Verify before/while building

- Navidrome `getPlaylists`/`getPlaylist` are supported by the deployed version and
  song `path`s resolve well via `_paths_match` on real data (Phase 2). (Admin's own
  playlists → the multi-library username issue is out of scope until Phase 5.)
- The `playlists` **table rebuild** preserves all existing grabber playlists/tracks
  (Phase 1 test).
- Diff/upsert sync preserves dates and `is_new` correctly across repeated syncs.

## Testing

- Migration rebuild against existing grabber data; source-aware sync diff; Navidrome
  matching; `/api/run/queue?playlist=`; player read-scope (can list run playlists,
  cannot manage).
- Migrations additive/guarded; safe on existing DBs.

## Docs (when shipping user-facing behavior)

- Update `README.md` **and** `DOCKERHUB_README.md` (project rule) + `CHANGELOG.md`.
- No new required env vars (reuse `navidrome_*`).
