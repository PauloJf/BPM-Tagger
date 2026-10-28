# Plan: one Player for Run and Listen

> Current status of all plans is tracked in [STATUS.md](STATUS.md). This is step 6
> of [ui-revamp.md](ui-revamp.md).

Status: **Design proposal** (2026-10-28). Nothing built. Mockups: a private
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

One **Player** page with a `Run | Listen` switch at the top. Everything is shared
except one control deck:

| Shared | Run deck (lock on) | Listen deck (lock off) |
|---|---|---|
| Cover, title, waveform, transport, rating and dislike, lyrics, mini-player, source picker, queue panel | Target BPM and lock, the native → target pill, presets with "N ready", ±1/±5, offline chips | "Playing from" source card, Radio (from this source / similar), and a "Tempo lock is off — Lock to a pace" prompt |

The queue panel is the same panel in both: rows read `native · fold · stretch →
result` in Run and duration in Listen; the header action is **Rebuild** in Run and
**Add more** in Listen.

## Switching modes

- **Run → Listen:** release the lock, keep the queue, keep playing. Nothing to
  confirm; it only removes a stretch.
- **Listen → Run:** the one awkward moment, because a queue built without a
  pace may not fit one. A short dialog asks: **build a queue for this pace**
  (recommended: the current song carries on, the rest is replaced with tracks
  that fit, starred first) or **keep this queue and stretch it** (tracks that
  can't reach the target within the stretch limit play at native speed and are
  marked *off cadence*; the dialog says how many). Picking a preset inside the
  dialog changes the count live.
- Starting from Home's preset cards (`/run?bpm=N`) skips the dialog: that is an
  explicit "start a run", as it is today.

## Routes and entry points

- `/run` and `/listen` stay, as deep links that open the Player in that mode.
  The installed app's `start_url` (`/run`), Home's preset cards, `?bpm=` and the
  Settings anchor all keep working, and nothing in docs or bookmarks breaks.
- Navigation: one **Player** item under Play (Home · Player) once both modes
  live in it. The kiosk (below) keeps whichever labels fit its mode.
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

1. **Extract a shared shell.** Pull the cover, waveform, transport, queue panel
   and the one-screen mobile machinery out of `Run.tsx` (1,367 lines) and
   `Listen.tsx` (568) into shared components, with no visible change and the
   existing 52 Run and Listen tests as the safety net.
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

## Open questions

- **Name.** "Player" is accurate but generic; the app's own words are Run and
  Listen. Alternatives: keep both nav items pointing at the one page, or call the
  page "Now playing".
- **Radio and the lock.** Radio refills at native speed from the source; Run
  refills with tracks that fit. Does turning the lock on switch the refill, or
  does Radio simply turn itself off?
- **Remember the dialog choice?** A "don't ask again" would make Listen → Run
  one tap for people who always choose the same thing, at the cost of surprise.
- **Offline chips** belong to a cadence, so they live in the Run deck; check they
  still make sense when the lock is off and a queue is already downloaded.
