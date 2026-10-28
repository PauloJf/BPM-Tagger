# Plan: Libraries, user permissions and per-account 2FA

> Current status of all plans is tracked in [STATUS.md](STATUS.md).

Status: **Proposal** (2026-10-26). Nothing built.

## Where we are

- **One music folder.** `MUSIC_DIR` is a single root: `scan/scanner.py` walks it
  (`os.walk`) and the watcher schedules it; `web/state._assert_in_music_dir` is the
  one path check; about 20 files reference it.
- **Accounts.** The admin lives in `settings.json` (username, password hash, and
  the only TOTP secret plus recovery hashes). Named player users live in the
  `players` table (`username`, `password_hash`, `full_access`, `enabled`,
  `accent_hue`, `listen_mode`). A shared Guest login (`RUN_PASSWORD`) has no row.
- **Scope is by playlist.** A non-full-access player sees only the tracks of the
  playlists in `player_playlists` (`get_run_candidates_for_playlists`, …). The
  phase-5 plan calls this "an organizational boundary, not a security one":
  `_PLAYER_ALLOWED` in `web/app.py` is a default-deny allowlist of *endpoints*,
  not a filter on *data*.
- **The flaw in playlist scope.** A new file is invisible to a scoped player until
  someone adds it to one of their playlists.

## Goals and non-goals

**Goals**
1. **Libraries:** several music folders, each assignable to accounts, so a member
   sees only their folders and the admin sees everything.
2. **Sharing without copies:** two accounts can have the same song with one file
   on disk and one track row.
3. **Real permissions:** roles, a hard (server-enforced) data boundary, 2FA on
   every account, self-service password change.

**Non-goals:** no symlink/hardlink tricks and no moving of files behind the
user's back; no change to ratings, plays, journal or queue state (already
per-account); no Subsonic changes (its fate is open, see questions).

## Concepts

**Library.** A named root folder (`libraries(id, name, path)`). Roots must be
disjoint: no root inside another, compared after `realpath`; rejected at save time.
A file's *home library* is the root its path sits under, stored as
`tracks.library_id` so scoping is an indexed lookup. `MUSIC_DIR` becomes the first
library ("Music") on upgrade, so a single-folder install is unchanged.

**Membership (sharing).** `track_libraries(track_id, library_id)` holds *extra*
grants ("also visible in Kids"). A track is visible to an account when its home
library or any extra library is one of the account's libraries. The admin sees one
row per track, with a badge listing its libraries. Cleanup is in Python (no FKs in
this codebase), like `player_playlists`.

**Account scope.** An account has a set of libraries (none assigned = all, so
today's accounts keep working) and an optional playlist set that **narrows** it
(`visible = libraries ∩ playlists`). Existing scoped players keep exactly today's
behaviour: all libraries plus their playlists. Libraries are the security
boundary; the playlist set is a convenience filter.

**Roles.** Three presets, no free-form ACL:

| Role | Can |
|---|---|
| **admin** | everything: tagging, settings, users, libraries, delete |
| **member** | browse, play (Run and Listen), rate, own queue and journal, inside their libraries |
| **kiosk** | play only: no ratings, no saved queue; replaces the shared Guest login |

`listen_mode` (off / on / default / only) stays as a per-account option for now.

**Two-factor.** TOTP secret and recovery-code hashes move per account (columns on
the account table). The two-step login that exists for the admin
(`api/auth.py`, `web/totp.py`) is generalised. A setting chooses the policy:
*optional* (default), *required for admin*, *required for everyone*. The admin can
reset an account's 2FA.

## Enforcement

One scope object per request, from the session: `visible_libraries(owner)` →
`None` (all) or a list of ids.

- **Queries.** Every track-returning query takes it: whole-library queries
  (`get_run_candidates(None)`, `get_listen_library`, the Library table, search,
  artists and albums, related and similar, stats leaderboards). The Subsonic
  `scope` parameter (if the API stays) is the same hook.
- **Paths.** `_assert_in_music_dir(path)` becomes `_assert_in_library(path)`: the
  path must be under *some* root, and for non-admins the track must be visible.
  Covers audio streaming, images, lyrics, loudness, waveform.
- **Playlists** stay admin-owned and global; their entries are filtered by the
  viewer's scope, so counts differ per viewer.
- **Safety net.** A parametrised test over every endpoint in `_PLAYER_ALLOWED`:
  an account limited to library A cannot read, stream or list a track from
  library B. A new endpoint without a scope check fails it.

## Duplicates across libraries

Two separate files for the same song (found by the existing normalised
artist+title/ISRC match) in different libraries stay separate tracks. The
Duplicates page resolves a cross-library pair as *keep one file, soft-delete the
other (trash), add both libraries to the survivor*, which converts two copies into
one shared track. Within one library it behaves as it does today.

## Steps

Each ships alone.

1. **Libraries in the core** *(smallest, admin-visible only).* `libraries` table,
   `tracks.library_id` + backfill, multi-root scanner and watcher,
   `_assert_in_library`, Settings → Libraries (add / rename / remove, with the
   overlap check), a library filter on the Library page. Docker docs for several
   mounts. No account changes. Core-isolation test stays green (libraries live in
   `scan/` and `db/`, scoping in `web/`).
2. **Library scope for accounts.** Assign libraries to a player user; enforcement
   as above; the isolation test. Playlist assignment becomes the narrowing filter.
3. **Sharing.** `track_libraries`, a *Share to library* action on a track or a
   selection, the admin badge, the cross-library Duplicates resolution.
4. **Roles.** member / kiosk presets; migrate `RUN_PASSWORD` to a kiosk account
   (same hash, named "guest") instead of a rowless login.
5. **Per-account 2FA** and the policy setting; self-service password change; a
   session list with revoke.
6. **Admin into the account table** *(optional, later).* Removes the special case
   in `settings.json`.

## Verification

Backend: `pytest -q`, `ruff check bpm_tagger/ tests/`, and the isolation test.
Frontend: typecheck, `npm test`, build. Manual: two accounts with disjoint
libraries on a copy of a real database, checking Library, Run, Listen, search,
stats, direct audio URLs and the Related panel for leaks. User-facing steps update
`README.md` and `DOCKERHUB_README.md` with the commit.

## Risks

- **Leaks.** The risk is an endpoint that returns track data without a scope.
  Mitigation: the single scope object and the endpoint-wide test.
- **Breadth.** `MUSIC_DIR` is used in about 20 files; step 1 touches the scanner,
  trash, path templates, image and lyrics handlers.
- **Existing installs.** Migration must leave a single-folder install and every
  existing player user behaving identically.

## Open questions

- **Moved files.** The key is `file_path`; a move across libraries looks like a
  new track. `file_hash` is `size:mtime`, which a move preserves, so rename
  detection may be cheap, but it is unverified. Needed so ratings and plays
  survive "move to library".
- **Admin in the account table:** step 6 now, or leave it?
- **Members and playlists:** may a member create their own local playlists (today
  playlist writes are admin-only)?
- **Subsonic:** if it stays, library scope must reach `db/subsonic.py`.
- **2FA policy default:** optional, as proposed?
- **Guest migration:** auto-create the kiosk account from `RUN_PASSWORD`, or ask?
