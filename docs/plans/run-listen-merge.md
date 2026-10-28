# Plan: one Player for Run and Listen

> Current status of all plans is tracked in [STATUS.md](STATUS.md). This is step 6
> of [ui-revamp.md](ui-revamp.md).

Status: **Design agreed** (2026-10-28), nothing built. The four open questions are
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
2. **Add the mode switch** by rendering one page with two decks, behind the
   same routes. `/run` and `/listen` both mount it.
3. **The switch dialog**, with the lock-and-stretch counts computed from the
   queue already in memory (the same `lockRate` the player uses).
4. **Navigation and Home**: the single Player item, Home's Resume card linking to
   the right mode, the remembered mode.
5. **Remove the duplicated pages and CSS**; update README and screenshots.

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

No open questions remain; build step 1 (the shared shell) is done and the next step is 2, the mode switch.
