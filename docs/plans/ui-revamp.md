# Plan: UI revamp — information architecture and a Home screen

> Current status of all plans is tracked in [STATUS.md](STATUS.md).

Status: **In progress** (proposed 2026-10-25). Steps 1 (navigation regroup) and 2 (Home v0: *Start a run*, *Needs attention*, first-run call to action, `/home` landing) are built on `feature/ui-revamp-nav`; steps 3–6 are open. Steps 1–2 are frontend-only.

Beyond the plan's list, the Library page also got the **Tempo strip** (a clickable library BPM histogram plus run-preset chips, replacing the loose BPM/± inputs), a **More filters** popover (four filters stay on the toolbar), and the step-4 table density work (no detector column, no "ok" badge, hover-only row actions). That covers step 4 except the optional detector toggle: the column is simply removed, since the track page already shows it.

The **Artists** view follows the mockup: a per-artist tempo mix, "N of M" fit counts, the shared Tempo strip (carried between Tracks and Artists through `LibraryTabs`), a sort menu and an A–Z rail. It needed one backend change: `/api/artists` now returns `mix` per artist and, given a tempo window, `fit`. Not built from the Artists mockup: the per-row play button and the header's "Shuffle N artists".

**Step 3 is built too:** one shared run-readiness query (`useRunReadiness`) feeds Playlists, Home and Run; the Run preset buttons show "N ready" for the chosen source (hidden for named player accounts, whose API access doesn't include the numbers); and Home gains a **Resume** card (from the player's own queue) and **Recent runs** (the last three journal rows). Not built: the mockup's per-run cadence trace (the journal stores only an average), a "Start over" button, and the library-snapshot block (v2 in the plan).

**Albums** follows its mockup too: a cover grid with an "N of M fit" badge (or "N half-time" when every match needed folding), a fit bar, the shared Tempo strip, the fit filter and a sort menu. `/api/albums` gains `fit` and `fit_folded` for a tempo window. Not built: the mockup's "Has starred" and "Needs review" chips, and the header's "Shuffle N albums".

Evidence: a UI audit on a copy of a real 1,455-track library (desktop and mobile).
Findings that drive this plan:

- The navigation is a list of features, not of intent. Run and Listen sit under
  "Library"; the app opens on a track table (`/tracks`), which is a tagging tool.
- The Playlists page shows how many tracks are ready at each preset ("Easy 155 ·
  547 ready"). The Run page, where you pick a pace, doesn't.
- Settings is one long page of ~21 sections with Appearance first.
- Library table: titles truncated while the detector column is wide and mostly
  empty; the filter chip row overflows on desktop.

## Goals and non-goals

**Goals:** make the runner the front door, matching the three pillars (BPM
engine · Run · library companion) and the docs' positioning; keep every change
small and shippable on its own.

**Non-goals (for now):** no backend changes in steps 1–2; no change to the
player-role (kiosk) shell; no merge of Run and Listen yet (step 6, needs its own
design); no visual restyle.

## 1. Information architecture

Today (`components/Nav.tsx` → `buildSections`):

| Section | Items |
|---|---|
| Library | Library · Run · Listen · Playlists |
| Tagging | BPM Review · Duplicates |
| System | Stats · Settings · About |

Proposed:

| Section | Items | Note |
|---|---|---|
| **Play** | Home · Run · Listen | the runner's side; first |
| **Library** | Library (tracks / artists / albums) · Playlists | browsing and organising |
| **Maintain** | BPM Review (badge) · Duplicates · Stats | the tagger's side |
| System | Settings · About | |

Routes stay as they are, plus one new route, `/home`. The match lists for
active-state highlighting don't change. The mobile menu and the desktop sidebar
both read `buildSections`, so both follow.

Landing: `/` goes to `lastPage() ?? "/home"` (was `/tracks`). The installed PWA
keeps `start_url=/run`, so a runner still opens straight on Run; Home is for the
browser and desktop.

## 2. Home (admin and guest shell only)

Built only from endpoints that already exist.

| Block | Shows | Source |
|---|---|---|
| **Start a run** | one card per preset: name, BPM, "N ready"; tap → `/run?bpm=N` (the existing deep link) | `GET /api/run/readiness` (`library` counts) |
| **Needs attention** | review count → `/review`; decode problems / errors → library filter; scan state. Hidden when all zero | `GET /api/stats` (`summary`) |
| **Resume** *(v1)* | "Continue your queue" when a saved queue exists | `GET /api/player/state` |
| **Recent runs** *(v1)* | last 3 runs: date, duration, avg cadence | `GET /api/stats/runs` |
| **Library snapshot** *(v2)* | tracks, artists, the BPM histogram | `GET /api/stats` (`bpm_distribution`) |

Empty library or nothing tagged yet: a single "Scan your library" call to action
in place of the blocks (doubles as the first-run screen).

## 3. Steps

Each step ships alone and leaves the app working.

1. **Regroup the navigation** *(smallest)*. Edit `buildSections` in `Nav.tsx`:
   Play / Library / Maintain / System, same routes and icons, Home item added in
   step 2. Update or add a test for the section order. No routing, API or page
   changes. Done when: typecheck, `npm test` and `npm run build` pass, and the
   sidebar and mobile menu show the new groups.
2. **Home v0.** `pages/Home.tsx` with *Start a run* and *Needs attention*;
   `/home` route in `App.tsx`; nav item; landing change for `/`. Component test
   with mocked queries (the pattern in `Playlists.test.tsx`). README screenshot
   and a CHANGELOG line.
3. **Readiness on the Run page, plus Home v1.** Share one `run-readiness` query
   hook between Playlists, Home and Run; show "N ready" on the Run preset
   buttons; add *Resume* and *Recent runs* to Home.
4. **Library table density.** Wider title column, detector column hidden by
   default, filter chips that scroll or collapse, "–" instead of "0" for unrated.
5. **Settings regroup, reorder only.** Run & Player (Playback, Run Mode, Ratings
   & picking, Player access) · Library & integrations (Library, Navidrome,
   Subsonic API, Artwork, Lyrics, ISRC) · Security (Admin sign-in, Two-factor,
   Login protection) · Maintenance (Scan Behavior, Operating Mode, Trash, Deleted
   Tracks, Version, Restart) · Appearance last. No section content changes.
6. **Merge Run and Listen into one player.** Own design doc first.

## 4. Verification (every step)

`cd frontend && npm run typecheck && npm test && npm run build`, then look at it
in the browser against a copy of a real library, on desktop and at 375 px wide.
A user-facing change also updates `README.md` and `DOCKERHUB_README.md` in the
same commit (project rule).

## 5. Open questions

- PWA `start_url`: keep `/run` (assumed here) or move to `/home`?
- The sidebar subtitle still reads "for Navidrome". Rename to match the
  running-player positioning, or leave it?
- Should Home ever be offered to the player role? Assumed no: that shell stays
  Run / Listen / About.
- The Subsonic API's place in Settings (Library & integrations in step 5) is
  provisional until its future is decided.
