# Design: Phase 5 — Local player users & source-agnostic grabber queue

> Design proposal for Phase 5 of [playlists-integration.md](playlists-integration.md).
> **PLAN ONLY** — no code yet. Decided 2026-07-19: Phase 5 auth uses **local player
> users** (accounts in BPM Tagger's own DB), **not** Navidrome-credential login. See the
> "Locked decisions" block in the parent plan.

## Goal & non-goals

**Goal:** turn today's single shared Run-only password into per-user player accounts, each
scoped to a set of playlists (or flagged full-access), managed from a Settings → Users
panel. Plus the follow-ons bundled into Phase 5: periodic playlist sync, a "play
everything / force tempo" run toggle, and a source-agnostic "queue missing in the grabber"
path so Navidrome (and future) playlists can queue their missing tracks.

**Non-goals (explicitly deferred, per the locked decision):**
- No Navidrome credential relay/validation. Playlists keep being read with the **admin's**
  Subsonic creds, so the multi-library "read another user's playlist" problem never arises
  and login works with Navidrome down.
- No per-user scrobbling/stars — plays stay attributed to the single admin Navidrome
  account, exactly as today. (An optional "link to a Navidrome user" field can come later.)
- Player association is an **organizational boundary, not a security one** (see §3).

---

## 1. Schema (`db.py`, additive migration)

Two new tables, both plain `CREATE TABLE IF NOT EXISTS` added to `_create_grabber_tables`
(or a sibling `_create_player_tables` called from `_migrate`). **No table rebuild** — unlike
the Phase 1 `playlists` generalization, these are brand-new tables, so the migration is
purely additive and safe on every existing DB.

```sql
CREATE TABLE IF NOT EXISTS players (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,     -- stored lowercased; login is case-insensitive
    password_hash TEXT NOT NULL,            -- werkzeug generate_password_hash (same as RUN_PASSWORD)
    full_access   INTEGER NOT NULL DEFAULT 0,
    enabled       INTEGER NOT NULL DEFAULT 1,   -- disable without deleting (also invalidates sessions)
    created_at    TEXT,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS player_playlists (
    player_id   INTEGER NOT NULL,
    playlist_id INTEGER NOT NULL,
    PRIMARY KEY (player_id, playlist_id)
);
CREATE INDEX IF NOT EXISTS idx_pp_player ON player_playlists(player_id);
```

**Referential cleanup (no SQLite FKs in this codebase — enforce in Python):**
- `delete_playlist(pid)` must also `DELETE FROM player_playlists WHERE playlist_id = ?`.
- `delete_player(pid)` must also `DELETE FROM player_playlists WHERE player_id = ?`.

**New DB methods** (mirroring the existing `list_playlists` / `add_local_playlist` style):
`list_players()`, `get_player(id)`, `get_player_by_username(name)`, `add_player(username,
hash, full_access, playlist_ids)`, `update_player(id, ...)`, `set_player_password(id, hash)`,
`delete_player(id)`, `set_player_playlists(id, ids)`, `list_playlists_for_player(id)`,
`playlist_ids_for_player(id) -> set[int]`, `touch_player_login(id)`.

---

## 2. Auth (reuse `auth.py` hashing; session carries player id)

### New helper in `web/auth.py`

```python
def verify_player(username: str, candidate: str) -> Optional[dict]:
    """Return the enabled player row whose username+password match, else None.
    Reuses werkzeug check_password_hash — the same primitive verify_ui_password /
    verify_run_password already use."""
```

### Login flow (`web/api/auth.py::api_login`)

Today login is **password-only**: admin password wins, else the run password grants the
`player` role. Phase 5 adds an **optional `username`** field to the JSON body and resolves
in this order:

1. **`username` present** → `verify_player(username, password)`.
   - Match → `role="player"`, `session["player_id"] = row["id"]`,
     `session["pw"] = password_stamp(row["password_hash"], "")`.
2. **`username` blank** (back-compat — unchanged UX for existing installs):
   - `verify_ui_password` → `role="admin"` (stamp `PW_STAMP`).
   - else `verify_run_password` → `role="player"`, `player_id=None` (the shared **guest**;
     see §5), stamp `RUN_PW_STAMP`.

The lockout counters, `session.permanent`, and CSRF handling are untouched. Player-user
sessions still get `role="player"`, so they inherit the longer `RUN_SESSION_SECONDS` cookie
lifetime from `_RoleSessionInterface` with no change there.

### Session-stamp validation for per-user passwords

`login_required` currently accepts a session whose `pw` stamp is in `{PW_STAMP,
RUN_PW_STAMP}`. Player users have **per-user** hashes, so those two global stamps can't
cover them. Add one branch:

```python
# In login_required, before the global-stamp check:
if session.get("role") == "player" and session.get("player_id") is not None:
    row = state().db.get_player(session["player_id"])
    if not row or not row["enabled"]:
        return 401                      # deleted or disabled → logged out immediately
    if session.get("pw") != password_stamp(row["password_hash"], ""):
        return 401                      # password was reset → logged out
    g.player = row                      # stash for §3 scoping (fresh full_access every request)
    return f(...)
# else: existing admin / guest global-stamp check, unchanged
```

This is one indexed single-row read per player request (Run endpoints are low-traffic, not
hot), and it buys three things for free: **password reset, disable, and delete all
invalidate the user's sessions immediately** — the same guarantee `password_stamp` gives
the admin/run passwords today. Admin and guest paths are completely unchanged. (If profiling
ever shows this matters, cache `id → stamp` in `app.config`, invalidated on user mutations —
but start with the correct-by-construction lookup.)

`g.player` is `None` for admin and guest sessions → treated as full-access in §3.

### Frontend: login + identity

- **`Login.tsx` gains an optional username field** (blank = today's password-only flow →
  admin or guest). Existing installs and the installed PWA keep working with no re-login
  and no UX change unless a username is typed.
- **`GET /api/me` returns the player identity** — `username` and `full_access` for
  player-user sessions (`null` username + `full_access: true` for admin and guest) — so
  the Run source picker can hide the Whole-library/Starred sources from restricted users
  instead of offering sources that 403 (§3). The server still gates regardless.

---

## 3. Scoping

Define one helper used by both run endpoints:

```python
def _run_scope():
    """(full_access: bool, allowed_ids: set[int] | None) for the current session.
    Admin + guest + full_access players → (True, None). Restricted player →
    (False, {playlist ids they're associated with})."""
    p = getattr(g, "player", None)          # set by login_required for player-user sessions
    if p is None or p["full_access"]:
        return True, None
    return False, state().db.playlist_ids_for_player(p["id"])
```

### `GET /api/run/playlists`
Filtered to the session user's playlists; **all** when full-access.

```python
full, allowed = _run_scope()
rows = db.list_playlists() if full else db.list_playlists_for_player(g.player["id"])
```

### `GET/POST /api/run/queue`
- **`playlist=<id>` given:** if not full-access, require `id in allowed` → else **403**
  (`{"error": "forbidden"}`). This is checked right after the existing
  `st.db.get_playlist(playlist_id)` existence check in `run.py`.
- **Default pool (no playlist / `library` / `starred`):** the library & starred pools are
  **full-access-only**. A restricted player who hits `/api/run/queue` with no playlist →
  **403**. (The Run page won't offer "Whole library"/"Starred" to a restricted user, but
  the server gates it regardless — never trust the client.)

### Media streaming stays path-validated only — say so in code
`media.audio` remains gated by `_assert_in_music_dir` **only**; it is deliberately **not**
scoped by playlist membership. Add a comment where the streaming route validates the path:

```python
# NOTE: playlist association is an *organizational* boundary, not a security one.
# A player scoped to playlist X can still stream any path under MUSIC_DIR if they
# learn/guess it — media is gated by path-validation alone. This is intentional:
# Run-mode curation decides what a user is *offered*, not a DRM wall around bytes.
```

`media.audio` therefore stays in `_PLAYER_ALLOWED` unchanged. The two new player-facing
reads (`api_run_playlists`, `api_run_queue`) are already listed there; the Users-admin
endpoints (§4) are **not** added to the allowlist, so the default-deny gate keeps players
out of them automatically.

---

## 4. Settings → Users admin panel

New admin-only blueprint `web/api/players.py` (registered in `app.py`; **not** in
`_PLAYER_ALLOWED`). Every route `@login_required` **and** rejects `session.role == "player"`
(mirroring the `if session.get("role") == "player": 403` guard already in
`api_settings_run_password`).

| Route | Body / effect |
|---|---|
| `GET /api/players` | list: `{id, username, full_access, enabled, playlist_ids, last_login_at}` |
| `POST /api/players` | `{username, password (≥8), full_access, playlist_ids}` → create (409 on dup username) |
| `PATCH /api/players/<id>` | any of `{full_access, enabled, playlist_ids}` |
| `POST /api/players/<id>/password` | `{new_password (≥8)}` → reset (invalidates that user's sessions) |
| `DELETE /api/players/<id>` | delete user + its `player_playlists` rows |

Password rules reuse the `api_settings_run_password` conventions: min 8 chars,
`generate_password_hash`, and reject a username/password colliding with the admin password.

**Frontend** — a **Users** section in Settings (admin-only, like the Run-password box):
users table, "Add user" form (username + password + full-access checkbox + playlist
checkboxes), per-row full-access toggle, enable/disable, reset-password, delete. Playlist
checkboxes are fed by the existing `GET /api/playlists`.

---

## 5. `RUN_PASSWORD` migration — two options

**Option A — keep it as a shared "guest" full-access player (RECOMMENDED).**
Zero behaviour change on upgrade: existing installs keep logging in with the run password
(blank username → guest path in §2), and the guest is **full-access**, so it still sees
every playlist — identical to today's "all playlists shared to the single Run player." Named
users are purely additive. An admin can later disable the guest via the existing
`POST /api/settings/run-password {disable:true}` once real users exist. **No forced action,
no broken logins, no deployed-PWA re-login.**

**Option B — retire `RUN_PASSWORD`, require named users.**
Cleaner mental model (one auth mechanism), but on upgrade every existing install's run login
stops working until the admin creates a user, and any run-password-based guidance/PWA setup
must be redone. Migration friction with no functional gain over A (A can converge on the
same end state voluntarily).

**Recommendation: A.** Ship guest-as-full-access-player for a frictionless upgrade; document
that admins can disable it once they've created scoped users. Keep `verify_run_password`
and its settings box exactly as-is.

---

## 6. Periodic playlist sync (share the mechanism with the open star/play-count follow-up)

STATUS.md already lists an open follow-up: *periodic/background sync for stars and play
counts*, with two candidate approaches — piggyback on scan completion, or an interval thread
like the grabber's `SpotifySync` loop. **Playlist sync wants the exact same machinery**, so
build **one** scheduler rather than three.

**Proposal:** a small `PeriodicSync` thread **started from `main.py`** — deliberately
*not* owned by `GrabberService`, because Navidrome playlist sync and star/play-count
sync must run with the grabber disabled (the parent plan's "decouple from the grabber"
rule). It wakes every `sync_interval_minutes` and runs whichever jobs are enabled:
- **Playlists:** for each `enabled` playlist, call the source-appropriate sync —
  `g.sync.sync_playlist(id)` (Spotify: needs grabber + live connection) /
  `sync_navidrome_playlist(db, cfg, id)` (Navidrome: needs creds). **Local never syncs.**
  Skip gracefully when a source's prerequisites are absent (don't error the whole tick).
- **Stars / play counts:** the same tick calls the existing manual-sync entry points
  (`integrations/star_sync.py`, `integrations/play_sync.py`), closing the STATUS.md
  follow-up in the same change.

**Settings:** `sync_interval_minutes` (0 = off, default off), plus the existing per-feature
toggles (`NAVIDROME_SCROBBLE`, star-sync enable, etc.) gate which jobs run. One thread, one
interval, feature toggles decide the work — no duplicated loops. Manual Sync buttons stay.

Note this unifies with the STATUS.md item and recommend implementing them together.

---

## 7. "Play everything, force tempo" toggle

Today `run.py::_matches` keeps only candidates whose octave-folded BPM lands within
`tol` of the target (`dev <= tol`). The toggle **drops the tolerance filter**: include the
whole (scoped) pool and force each track to the target via `playbackRate`.

- **Where:** a run preset `run_force_tempo` (a `run_*` key, so it's already returned to
  players by the filtered settings endpoint) **and/or** a per-request `force: true` in the
  queue body. Recommend the per-request flag as the source of truth, with the setting as the
  default — the player toggles it live without a settings write.
- **Behaviour when on:** `_matches` skips the `dev <= tol` gate (every candidate qualifies),
  still octave-folds to the *closest* multiple (minimizes the stretch), still sorts by
  starred / familiar / closest, and returns `rate = target/folded` as usual. Because octave
  folding already collapses ×½/×2, residual stretch for most tracks stays within roughly
  `[0.71, 1.41]`; **clamp** the emitted rate to a sane band (e.g. `[0.5, 2.0]`) so a genuine
  outlier can't produce a chipmunk/rumble artifact — surface the clamp in the response so the
  UI can note "forced."
- **Interaction with scoping:** force-tempo changes *which BPMs qualify*, not *which pool* —
  a playlist run still respects membership (§3), and the library/starred pool is still
  full-access-only. No scoping interaction.

---

## 8. Ride-along: source-agnostic "queue missing in grabber"

**Problem.** `grab_queue` and `enqueue_grab` dedupe on `spotify_track_id`. A **missing
Navidrome** track carries no Spotify id (`source_track_id` is a Subsonic song id;
`isrc`/`spotify_track_id` are `None` — see `navidrome_playlists._song_to_track`). So today
"Queue missing" only works for Spotify playlists. (Local playlists have no "missing" state —
every Local track is `have` by construction — so this is really about Navidrome and any
future non-Spotify source.)

**Don't invent a new mechanism — `suggestions.py::queue_suggestion` already solves exactly
this** ("enqueue a track that has no Spotify id"):
1. If Spotify is connected, search Spotify and adopt a **confident (≥0.9) best match**'s
   `spotify_track_id` + `isrc` (+ `album_artist`/`track_no`/`disc_no`/`year`) → gives full
   sid-based dedupe and better provider matching.
2. Else fall back to fetching the **ISRC** (there, from Deezer via `dz.track_isrc`).
3. `enqueue_grab(meta)` — dedupes on `sid` when present; plain insert otherwise.
And `queue_suggestion`/`queue-album` add a **normalized-key** guard —
`(normalize_title, normalize_artist)` against `get_active_grabs()` — so no-sid inserts don't
duplicate.

**Proposal: extract that logic into a shared helper and reuse it.**

- New `grabber/enqueue.py::enqueue_track(db, grabber, meta, *, playlist_track_id=None)` (or a
  `GrabberService.enqueue_track` method) that encapsulates: optional Spotify best-match
  adoption when connected → ISRC fallback when a lookup key exists → normalized-key dedupe →
  `enqueue_grab` with `playlist_track_id` linked. Returns the new id or `None` (already
  queued).
- **Refactor `queue_suggestion` to call it** (proves the extraction is faithful) — the
  Spotify-adoption block moves into the helper verbatim; suggestions keeps only its
  Deezer-`dz_track_id`→ISRC input.
- **Close the no-sid dedupe gap:** today `enqueue_grab`'s no-sid branch inserts
  unconditionally. Either extend `enqueue_grab` with an optional normalized-key `NOT EXISTS`
  guard, or have the helper do the `get_active_grabs()` check first (as `queue-album` does).
  Recommend folding the normalized-key guard into `enqueue_grab` so **every** caller is
  deduped, not just the ones that remember to check.

**New endpoint:** `POST /api/playlists/<id>/queue-missing` (admin, grabber-gated — hidden
when the grabber is off, per the parent plan's "Decouple from the grabber" rule). For each
non-tombstone `match_status='missing'` row, call the shared helper with `playlist_track_id`
linked so the playlist detail row reflects its queued state. Return `{enqueued, skipped,
total}`. Do **not** write the adopted `spotify_track_id` back onto the `playlist_tracks`
row: `sync_playlist_tracks` updates `_PT_META_COLS` — which includes `spotify_track_id` —
on every present-in-both row, and Navidrome sync supplies `None` there, so the next sync
would silently null the write-back. The resolved sid lives on the `grab_queue` item,
which is all the download pipeline needs.

**Caveat to surface in the UI (already noted in the parent plan):** queuing a missing
Navidrome track **re-downloads** it from the grabber's providers (Deezer/yt-dlp) by
metadata — it is *not* a copy from Navidrome.

---

## Migration & test checklist

- **Migration:** additive `CREATE TABLE IF NOT EXISTS` for `players` + `player_playlists`
  (no rebuild). Safe on existing DBs; existing installs boot unchanged with zero users and
  the guest password intact.
- **Auth tests:** username+password → player session; wrong/disabled/deleted user → 401;
  password reset invalidates the session (stamp mismatch); blank username still logs in
  admin and guest as today; `/api/me` reports `username`/`full_access` for a player user.
- **Scope tests:** restricted player sees only their playlists on `/api/run/playlists`; is
  403'd on another playlist's `/api/run/queue?playlist=`; is 403'd on the default
  (library/starred) pool; full-access player and guest see everything.
- **Admin-panel tests:** create/patch/reset/delete; player role 403'd on every `/api/players`
  route; deleting a playlist cascades `player_playlists`; deleting a user cascades its rows.
- **Force-tempo test:** with `force`, out-of-tolerance tracks appear with a clamped rate;
  without it, behaviour is unchanged.
- **Queue-missing test:** a missing Navidrome track enqueues via the shared helper (sid
  adopted when Spotify connected, else metadata-only), dedupes on re-queue, and links
  `playlist_track_id`; `queue_suggestion` still behaves identically after the extraction.
- **Periodic-sync test:** interval tick syncs enabled Spotify/Navidrome playlists, skips
  Local and skips gracefully when a source's prerequisites are missing; `0` disables it.

## Docs (when shipping)

Per project rule, update `README.md` **and** `DOCKERHUB_README.md` + `CHANGELOG.md`. No new
**required** env vars — `RUN_PASSWORD` stays as the guest fallback; `sync_interval_minutes`
and `run_force_tempo` are optional settings with off/false defaults.

## Suggested build order

1. Schema + DB methods (players / player_playlists) — pure groundwork.
2. Auth: `verify_player`, login username branch, `login_required` player-stamp branch.
3. Scoping: `_run_scope`, filter `/api/run/playlists` + gate `/api/run/queue` + media comment.
4. Users admin API + Settings panel.
5. Extract `enqueue_track`; refactor `queue_suggestion`; add `queue-missing` endpoint + button.
6. "Force tempo" toggle.
7. Periodic sync scheduler (jointly with the STATUS.md star/play-count follow-up).
