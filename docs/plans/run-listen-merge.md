# Plan: one Player for Run and Listen

> Current status of all plans is tracked in [STATUS.md](STATUS.md). This is step 6
> of [ui-revamp.md](ui-revamp.md).

Status: **Built** (2026-10-28), steps 1–5 on `feature/ui-revamp-nav`. The four open questions are
answered (see Decisions at the end). Mockups: a private
Design canvas, "BPM Tagger Player Mockup" (desktop with a working Run/Listen
switch, phone Run, phone Listen, and the switch dialog).

## Why

Run and Listen are two pages over one engine. `lib/player.tsx` already owns the
queue, the transport and the tempo lock; the pages differ in what they put
around it. Today that means two copies of the same chrome (cover, waveform,
transport, queue panel, source picker, ratings, lyrics) and, more importantly, a
seam in the middle of a run's day: warm up in Listen, go to Run, and the queue
you built is thrown away.

The observation the design rests on: **the only real difference is whether a
tempo lock is on.** `tempoLock` is already a field of the player's saved state, so
"Run" is the player with a lock and "Listen" is the player without one.

## The model

One page with a `Run | Listen` switch at the top. Its title follows the mode (the
page reads "Run" or "Listen"), so it never needs a name of its own. Everything is shared
except one control deck:

| Shared | Run deck (lock on) | Listen deck (lock off) |
|---|---|---|
| Cover, title, waveform, transport, rating and dislike, lyrics, mini-player, source picker, queue panel | Target BPM and lock, the native → target pill, presets with "N ready", ±1/±5, offline chips | "Playing from" source card, Radio (from this source / similar), and a "Tempo lock is off — Lock to a pace" prompt |

The queue panel is the same panel in both: rows read `native · fold · stretch →
result` in Run and duration in Listen; the header action is **Rebuild** in Run and
**Add more** in Listen. In Run the header says the queue "tops up with tracks
that fit", because that is what the refill does while the lock is on.

## Switching modes

- **Run → Listen:** release the lock, keep the queue, keep playing. Nothing to
  confirm; it only removes a stretch.
- **Listen → Run:** the one awkward moment, because a queue built without a
  pace may not fit one. A short dialog asks: **build a queue for this pace**
  (recommended: the current song carries on, the rest is replaced with tracks
  that fit, starred first) or **keep this queue and stretch it** (tracks that
  can't reach the target within the stretch limit play at native speed and are
  marked *off cadence*; the dialog says how many). Picking a preset inside the
  dialog changes the count live. The dialog always appears, with the previous
  answer pre-selected, so repeating a choice is one tap on **Start**.
- Starting from Home's preset cards (`/run?bpm=N`) skips the dialog: that is an
  explicit "start a run", as it is today.

## Routes and entry points

- `/run` and `/listen` stay, as deep links that open the Player in that mode.
  The installed app's `start_url` (`/run`), Home's preset cards, `?bpm=` and the
  Settings anchor all keep working, and nothing in docs or bookmarks breaks.
- Navigation: **two items, one page.** Run and Listen stay as separate entries
  under Play (Home · Run · Listen); each opens the page in its mode and the
  active one follows the switch. Nothing is renamed, and the kiosk keeps just
  the labels that fit its mode.
- The last mode used is remembered per device, so reopening the Player lands
  where you left it.

## The kiosk (player role)

The admin's `player_listen_mode` setting still decides what a player account may
reach; it is enforced server-side and that does not change. The UI follows:

| Setting | Player UI |
|---|---|
| `off` | Run only; no switch |
| `on`, `default` | Both, switch shown; `default` opens in Listen |
| `only` | Listen only; no switch |

A named player account also keeps today's restrictions (no tap tempo, no
readiness counts, no similar). The guest login behaves like the admin shell.

## Phone

The one-screen budget stays the rule. Both modes use Listen's bottom
**Playing | Queue** switcher, which frees Run's top Presets/Steps/Queue tabs: the
presets become a single scrolling chip row and the ±1/±5 steps move behind a
button in the transport row. The cover absorbs whatever height is left, so
Listen (no deck beyond a Radio chip) gets a bigger cover than Run. The mode
switch is a full-width pill under the title.

## Build order

Each step ships on its own and leaves both pages working.

1. **Extract a shared shell.** *Done (2026-10-28).* The glow layer, lyrics
   drawer, waveform strip, previous/play/next buttons and queue card moved into
   `components/PlayerShell.tsx`, and the audio-time / waveform / glow wiring into
   `hooks/usePlaybackSurface.ts`. Sizes that differ between the pages (button
   diameters, the queue card's bottom gap) are props, so nothing shifts. Proof of
   "no visible change": both pages were rendered in 45 states and their HTML
   compared before and after (identical, ignoring only CSS property order), on top
   of the existing Run and Listen tests. What is *not* shared yet, deliberately:
   the status notes (the two pills differ), the rating and dislike handlers (Listen
   keeps local optimistic state), and the rows inside each queue. Those differ in
   behaviour, so unifying them belongs with the mode switch, not before it.
2. **Add the mode switch.** *Done (2026-10-28).* `pages/Player.tsx` mounts the
   existing Run or Listen page for the route and draws a `Run | Listen` switch
   (`components/ModeSwitch.tsx`) through a slot both pages accept: beside the title
   on desktop, a full-width first row on a phone (no extra height; the cover
   absorbs it). The mode is the route, so `/run` and `/listen` stay valid deep links.
   Run → Listen releases the tempo lock and keeps the queue playing; Listen → Run
   changes nothing yet (the dialog is step 3). The kiosk is left alone: it has its
   own Run / Listen tabs in the nav and gets no switch. The two pages are still two
   components under one wrapper, not one component with two decks; that collapse
   waits for step 5, so each page's tests and layout rules stayed untouched.
   Deferred to step 4: Listen's radio refill under a lock, and the offline note.
3. **The switch dialog.** *Done (2026-10-28).* Listen → Run with a queue playing
   and no lock opens `components/SwitchToRunDialog.tsx`: pace chips (the four
   presets, plus the remembered pace if it isn't one), then **Build a queue for
   this pace** (the playing song stays first, the rest comes from
   `/api/run/queue`, starred first, with the preset's "N ready" shown) or **Keep
   this queue and stretch it** (the counts come from the queue in memory through
   `lockFits`, the yes/no twin of `lockRate`). The last answer is pre-selected and
   remembered; it never skips. With an empty queue or a lock already on there is
   nothing to ask and it just opens Run. Stretching points the run's refill at the
   source the queue came from. One correction to the design text above: tracks
   that can't reach the pace do not play at native speed; `lockRate` clamps them
   to the stretch limit, and Run already marks them ⚠, so the dialog says that.
   The Run page's presets, source and stretch settings moved to `lib/runSettings.ts`
   so the dialog and the page read them the same way.
4. **Navigation, Home and the loose ends.** *Done (2026-10-28).* An audit found
   most of this already true: the nav items are the routes, so the active one
   follows the switch; Home's Resume card links to Run while a lock is on and to
   Listen otherwise; and the installed app's start page already reopens the last
   page (`lib/lastPage.ts`), which *is* the remembered mode. What was missing and
   is now built: (a) **Run → Listen hands the run's source to Listen**, because
   starting a run clears the Listen source, which would have silenced Radio on the
   way back (a whole-library run becomes the `library` source); (b) the **queue
   header says "radio tops up with tracks that fit the pace"** under a lock, since
   the run refill, not the native-speed one, is what is topping it up; (c) the
   **offline note in Listen**, "N tracks saved offline · X MB", shown only when
   something is cached (so never over plain http, where there is no cache).
   The refill rule itself needed no engine change: the run refill already owns a
   locked queue, and the Listen radio refills are gated off by the lock and resume
   on release.
5. **Remove the duplication.** *Done (2026-10-28), smaller than planned.* Looked
   for what was still written twice and found little worth merging: the CSS was
   already shared (Listen uses Run's `run-*` shell classes; there is no Listen-only
   copy), and the code left over differs in behaviour on purpose. Run's rating and
   dislike handlers act on queue rows and don't revert on a failed write; Listen's
   act on the current track through a local map, because the player's
   `setTrackRating` updates the queue but not `current`. The two source pickers
   validate against different remembered keys, and the mobile layouts differ by
   design (Run's top Presets/Steps/Queue tabs, Listen's bottom Playing | Queue).
   Forcing those into one component would trade a little duplication for a risky
   rewrite of the most heavily tested page, so it was left alone. What was merged:
   the two copies of the switch-slot type became `ModeSwitchSlot`. Run and Listen
   remain two decks under the `Player` wrapper, which is what the design called
   for. README text is current; the screenshots are not refreshed (see below).

**Screenshots.** `docs/screenshots/11` and `12` show the kiosk's Run page, which has
no switch and is unchanged. `02` and `09` (the library) predate the nav regroup and
the tempo strip and want a re-shoot, which needs a proper capture tool rather than
the in-app browser pane.

## Risks

- `Run.tsx` is the most heavily tested page in the app and carries mobile
  layout rules that were hard-won (`.run-mobile-fill`, the queue-open height
  cap). Step 1 must not touch behaviour; the diff should be moves, not edits.
- The player's media-session controls, offline pre-load and run journal all key
  off the lock and the run source. Merging pages must not change when a "run"
  starts or ends (the journal derives it server-side from the lock events).
- The synced snapshot already carries `tempoLock`, so another device adopting a
  queue lands in the right mode without new fields.

## Decisions

Settled with the owner on 2026-10-28.

- **Name: two nav items, one page.** Run and Listen stay as entries; the page
  title follows the mode. (Rejected: a single "Player" item, "Now playing".)
- **Radio and the lock: refill with tracks that fit.** Locking to a pace keeps the
  queue topping up, but with the Run rule (tracks that can reach the cadence)
  instead of native-speed picks. Radio stays on and quietly changes what it
  picks; releasing the lock puts the Listen rule back. (Rejected: turning Radio
  off, which can end a long run with the queue; keeping native-speed refills,
  which would bring off-pace tracks into a run.)
- **The Listen → Run dialog always asks, remembering the last choice.** No
  "don't ask again" and no silent rebuild: the dialog is the only warning that a
  queue is about to be replaced.
- **Offline chips live in the Run deck only.** They are per-cadence downloads, so
  they stay beside the presets. In Listen a one-line note ("10 tracks saved
  offline · 82 MB") appears only when something is cached.

No open questions remain; all five build steps are done.
