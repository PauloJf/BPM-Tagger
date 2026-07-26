# Plan: Playlist playback + queue hygiene

> Current status of all plans is tracked in [STATUS.md](STATUS.md).

Status: ✅ **Done** — shipped in v2.10.0 (all four phases, 2026-07-26).
Code references are as of `3ad4d48` (v2.9.0) — prefer the symbol names over the line
numbers, another session is actively editing these files.

> **As built.** Phase 1 `9a83c9b`, Phase 2 `c7dd902`, Phase 3 `f0d9df0`, docs `95d2da5`.
> One plan claim was wrong: `Run.test.tsx` does **not** mount the player context — it
> `vi.mock`s `../lib/player` wholesale. The regression test there therefore asserts the
> *call order* (both setters fire after `playQueue`) against a recording stub, which
> guards the batching dependency directly; the real-state assertions live in the new
> `player.queuehygiene.test.tsx`. Note also that `enqueue`/`enqueueMany` delegate to
> `play`/`playQueue` on an empty queue, so those paths do clear the lock — unreachable
> mid-run, since a run always has a non-empty queue.

## Goal

Three related changes, all on the boundary between the playlist pages and the player:

1. **Play all / Shuffle all / Add to queue** on a playlist detail page. Today a playlist
   cannot be played anywhere except by picking it as a Run source.
2. **Clear the tempo lock and run source** when a non-run queue takes over, so starting a
   playlist doesn't silently hijack an in-progress run.
3. **Rename "Enqueue missing" → "Download missing"**, because item 1 introduces a second,
   unrelated meaning of "queue" onto the same page.

## 1. Play all / Shuffle all

### Current state

`PlaylistDetail.tsx` has exactly one action: `Enqueue missing` — a *grabber* action. There
is no way to play the playlist. Meanwhile `Album.tsx` already has the pattern we want:

```tsx
// Album.tsx :40–42
const toPT = (t: Track) => ({ path: t.file_path, title: t.title || basename(t.file_path),
  artist: t.artist || "", bpm: t.bpm, loudnessLufs: t.loudness_lufs });
const playAll = (shuffle: boolean) => { if (tracks.length) player.playQueue(tracks.map(toPT), 0, { shuffle }); };
```

`Tracks.tsx` (:167) does the same. **PlaylistDetail is the odd one out** — this is
consistency work, not a new concept.

### No new endpoint needed (with one exception)

`GET /api/playlists/<pid>/tracks` already returns everything a `PlayerTrack` needs on
`have` rows: `matched_file_path`, `title`, `local_artist`, `local_bpm`,
`local_duration_ms`. The mapping is:

```tsx
const playable = tracks
  .filter((t) => t.derived_status === "have" && t.matched_file_path)
  .map((t) => ({
    path: t.matched_file_path!,
    title: t.title || basename(t.matched_file_path!),
    artist: t.local_artist || t.artist || "",
    bpm: t.local_bpm ?? null,
    loudnessLufs: t.local_loudness_lufs ?? null,   // ← see below
  }));
```

**The exception — loudness.** v2.9.0 added volume levelling, and `PlayerTrack.loudnessLufs`
drives the attenuation. `PlaylistTrack` has no loudness field (`types.ts` :433–439 enrich
bpm/duration/artist/album only), so playlist playback would come out un-levelled while
album and library playback are levelled — an inconsistency users would hear immediately
on a mixed queue.

**Required:** add `local_loudness_lufs` to the enriched columns in
`db.get_playlist_tracks()` and to the `PlaylistTrack` interface. Small, but it must land
in the same change or playlist playback ships with a known audio defect.

### UI

Header actions on `PlaylistDetail`, mirroring `Album`:

- **▶ Play** (primary) → `playQueue(playable, 0, { shuffle: false })`
- **⇄ Shuffle** (secondary) → `playQueue(playable, 0, { shuffle: true })`
- **+ Add to queue** (bare/overflow) → `enqueueMany(playable)`

Disable all three when `playable.length === 0`, with a title explaining why ("no tracks
in this playlist are in your library yet"). Note the count on the button when it differs
from the playlist length — a 50-track Spotify playlist with 12 matched should read
`▶ Play (12)`, otherwise the button looks broken.

Respect the active tab filter (`tab` state): if the user is on the "have" tab, play what
they see. Playing the whole playlist while a filter is applied is the more surprising
behaviour.

### `enqueueMany` is required, not optional

`enqueue` (`player.tsx` :1304) reads `nav.current` and writes once:

```tsx
const enqueue = useCallback((track: PlayerTrack) => {
  const { queue, order } = nav.current;
  ...
  setQueue([...queue, track]);
  setOrder([...order, idx]);
}, [play]);
```

`nav.current` is refreshed by a `useEffect` **after render** (:318). Calling `enqueue` in
a loop over N tracks reads the same stale `nav.current` every iteration, so all but the
last track are dropped. Add a sibling that appends a batch in one write:

```tsx
const enqueueMany = useCallback((tracks: PlayerTrack[]) => {
  if (!tracks.length) return;
  const { queue, order } = nav.current;
  if (order.length === 0) { playQueue(tracks); return; }
  const base = queue.length;
  setQueue([...queue, ...tracks]);
  setOrder([...order, ...tracks.map((_, i) => base + i)]);
}, [playQueue]);
```

Add it to the `PlayerState` interface next to `enqueue`. The run auto-refill (:539–540)
already does exactly this inline and could later be folded onto it — **not** in this
change, it has subtle interactions with `runSource` and exclusion windows.

## 2. Clear the tempo lock and run source on queue takeover

### The problem

`playQueue` does not touch `tempoLock` or `runSource`. Only explicit sign-out does
(:1243–1251). So with a run in progress, hitting Play on a playlist (or an album, or
Tracks — this bug exists today) leaves:

- the **tempo lock** active, stretching the new playlist onto your running cadence;
- **`runSource`** pointing at the old run, so the mid-run auto-refill (:505–543) keeps
  topping the queue up from the *previous* source at the *previous* target.

The second one is the real bug: your playlist quietly gets run-mode tracks appended to it.

### The fix

Clear both inside `playQueue` and `play`:

```tsx
setTempoLock(null);
setRunSource(null);
```

**Do not** clear in `preview` / `endPreview` (a preview ducks and returns to the run), in
`enqueue` / `enqueueMany` / `playNext` (adding to a running queue shouldn't end the run),
or in the refill path (it uses `setQueue` directly, so it's unaffected).

### Why this doesn't break the Run page

`Run.tsx` starts a run in this order (:506–513):

```
player.playQueue(...)          // would now clear both
player.setRunSource(...)       // immediately re-set
player.setTempoLock(...)       // immediately re-set
```

All three are in one event handler, so React 18 batches them and the last write per key
wins. The run still starts locked and scoped.

⚠ **This ordering is load-bearing and invisible.** Add a comment at the `playQueue` call
site in `Run.tsx` saying so, and a regression test asserting that after `startRun` the
tempo lock and run source are set — otherwise a future refactor that reorders those three
lines silently kills run mode. `Run.test.tsx` already mounts the player context, so this
is cheap.

Also check `MiniPlayer.tsx` (:71–72), which toggles the tempo lock back on from a stored
value — it doesn't call `playQueue`, so it's unaffected, but it's the other place that
owns lock state and should be re-read when this lands.

### Decision recorded

Playing a playlist **exits run mode** rather than stretching the playlist onto your
cadence. Keeping the lock is a plausible feature ("run to this playlist") but a
surprising default, and the Run page's playlist source picker already covers that intent
properly — with cadence-aware selection, which a raw Play-all cannot do.

## 3. Rename "Enqueue missing" → "Download missing"

Once "Add to queue" exists on the same page, `Enqueue missing` is genuinely ambiguous —
one queues audio for playback, the other queues files for download.

| Location | Change |
|---|---|
| `PlaylistDetail.tsx` :103 | `Enqueue missing (N)` → `Download missing (N)`; `Enqueuing…` → `Downloading…` |
| `PlaylistDetail.tsx` :54 | rename the mutation `enqueue` → `downloadMissing` |
| `README.md` :83 | the "queue the missing ones separately with **Enqueue missing**" aside |

**Keep the endpoint** `POST /api/playlists/<pid>/enqueue-missing` as-is. It is exercised
by `tests/test_api_grabber.py` (:228–229) and renaming a route earns nothing here. Name
the UI honestly and leave the wire format alone.

Leave `CHANGELOG.md` history entries (:16, :54) untouched — they describe what shipped
under the old name.

## Not in scope (recorded so it isn't lost)

**Unifying the two queue views.** There are currently two renderings of
`player.orderedQueue`, each missing what the other has:

| | Reorder | Remove | Clear | BPM/fold/rate | ⚠ cap | Star/dislike |
|---|---|---|---|---|---|---|
| PlayerBar drawer (`PlayerBar.tsx` :76–118) | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| Run page panel (`Run.tsx` :1128–1190) | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ |

Extracting one `<QueuePanel>` used by both — with the tempo-lock columns keyed off
`tempoLock != null` rather than off which page it's on — would give reorder during a run
and BPM math in the drawer, from one implementation.

Deliberately **not** doing this as a `/queue` page: the queue is a transient control
surface consulted while you're doing something else, not a destination; `/queue` is
already the Grabber download queue; and route-bound queue UI cuts against the design
where playback and refill work on any route or with the phone locked (`player.tsx` :507).
It should also stay available to player users, who only have `/run` and are the people
most dependent on it — admin-gating it would break the kiosk case.

## Tests

- `frontend/src/pages/PlaylistDetail` — new component test: Play builds a queue from
  `have` rows only; the buttons disable with zero playable tracks; Shuffle passes
  `{ shuffle: true }`.
- `frontend/src/lib/player` — `enqueueMany` appends all N (the regression the stale
  `nav.current` would cause); `playQueue` clears `tempoLock` + `runSource`; `preview`
  and `enqueue` do **not**.
- `frontend/src/pages/Run.test.tsx` — after starting a run, tempo lock and run source are
  set (guards the batching-order dependency above).
- Backend: `get_playlist_tracks` returns `local_loudness_lufs` on `have` rows.
- Gates: `pytest -q`, `ruff check bpm_tagger/ tests/`, and in `frontend/`
  `npm run typecheck && npm test && npm run build`.

## Phases

1. **Player context** — `enqueueMany`, lock/source clearing in `playQueue` + `play`,
   tests. Self-contained, ships alone, fixes the album/Tracks hijack bug immediately.
2. **Loudness enrichment** — `local_loudness_lufs` through `get_playlist_tracks` +
   `PlaylistTrack`. Backend-only, ships alone.
3. **PlaylistDetail UI** — Play / Shuffle / Add to queue + the rename. Depends on 1 and 2.
4. **Docs** — README (playlist playback in the feature list, the :83 rename),
   DOCKERHUB_README if the feature list there mentions playlists, CHANGELOG.

Phase 1 is worth landing on its own regardless of the rest: the run-hijack bug is live
today on the Album and Tracks pages.
