# Plan: Run settings — collapse to a single "max stretch" slider

> Current status of all plans is tracked in [STATUS.md](STATUS.md).

Status: ✅ **Done** — shipped in v2.10.0 (`612b9b6`), breaking: two env vars removed.
Code references are as of `3ad4d48` (v2.9.0) — prefer the symbol names over the line
numbers, another session is actively editing these files.

## Goal

Delete **Match tolerance** (`run_tolerance_pct`) and **Force tempo**
(`run_force_tempo`). Promote **Max stretch** (`run_stretch_limit_pct`) to the single
authority over which tracks a run queues and how far they're stretched.

Three settings → one.

## Why

The three settings are not three concepts. Tolerance and max stretch measure **the same
quantity in the same units**:

| | Expression | Where |
|---|---|---|
| Server eligibility | `abs(target / folded - 1.0) <= tol` | `api/run.py` → `_matches` |
| Client playback clamp | `min(1+lim, max(1-lim, target/folded))` | `lib/player.tsx` → `lockRate` |

Both are the fractional distance of `playbackRate` from 1. A user has no way to know
that ±4% and ±15% are on the same scale.

Because they're the same quantity applied at different stages, **the two settings swap
authority depending on the force toggle**:

- **Force off** — tolerance (4%) is stricter than the stretch cap (15%), so every queued
  track is comfortably inside the cap. Max stretch never fires. It is inert.
- **Force on** — tolerance is bypassed, so max stretch becomes the real filter, except it
  applies *after* selection. Result: tracks sit in your queue that can never reach your
  cadence, carrying a ⚠ badge.

There is also a reachable broken configuration today: tolerance 20% with max stretch 15%,
force off. Tracks pass selection that are physically incapable of hitting the target, and
nothing warns you at build time.

### Why tolerance is the one to cut

Max stretch is a *quality* bound — past roughly ±20%, `preservesPitch` time-stretching
smears transients and warbles. That ceiling is real and has to exist.

Tolerance is a *preference* — "how close to native before I'll accept it". But
`_matches` already sorts by ascending deviation, and `count` already caps queue length.
So the queue is already best-matches-first, truncated. Tolerance's only remaining job is
to make the queue *shorter* than `count` when few tracks match closely — which reads as
a bug ("why is my queue only 6 tracks?") more often than as a feature.

Cut tolerance, and force tempo has no filter left to bypass, so it goes too.

## The model after this change

One slider. **Max stretch = how far from native a track may be pulled to reach your
cadence.** It is enforced twice:

1. **Selection** (new) — the server drops any candidate whose post-fold deviation
   exceeds the limit. Nothing unreachable ever enters the queue.
2. **Playback** (unchanged) — `lockRate` clamps `playbackRate` to the same bound.

Ordering is unchanged: closest-first, starred-first, familiar-first as configured.

## What gets deleted

| File | Symbol / line (as of `3ad4d48`) | Action |
|---|---|---|
| `bpm_tagger/config.py` | `run_tolerance_pct` (:244), `run_force_tempo` (:249) | delete both keys |
| `bpm_tagger/web/api/settings.py` | `run_tolerance_pct` (:240), `run_force_tempo` (:242) | delete from the save whitelist |
| `bpm_tagger/web/api/run.py` | `tol = ...` (:115), force parsing (:117–123), `force_raw` reads (:64, :73) | delete |
| `bpm_tagger/web/api/run.py` | `RATE_MIN`/`RATE_MAX` (:29), `_rate` clamp branch, `clamped` field | delete — see note below |
| `bpm_tagger/web/api/run.py` | `tolerance_pct=` and `forced=` in the response (:213, :216) | delete |
| `frontend/src/lib/types.ts` | `tolerance_pct` on `RunQueueResponse` (:65) | delete; add `stretch_limit_pct` |
| `frontend/src/pages/Run.tsx` | `FORCE_KEY` (:23), `force` state (:376–379), `forceToggle` (:917–946) and its two render sites (:1113, :1343, :1383), `forceArg` (:495–496) | delete |
| `frontend/src/pages/Settings.tsx` | "Match tolerance" field-row (:1381–1389), "Play everything (force tempo)" field-row (:1390–1393), `tolerance`/`forceTempo` state (:194, :331, :333) and payload keys (:1343, :1345) | delete |
| `tests/test_force_tempo.py` | whole file | delete |
| `docker-compose.yml` | `RUN_TOLERANCE_PCT` comment (:184) | delete |
| `README.md` | `RUN_FORCE_TEMPO` (:331), `RUN_TOLERANCE_PCT` (:353) rows | delete |
| `DOCKERHUB_README.md` | `RUN_FORCE_TEMPO` row (:94) | delete |

### Why `RATE_MIN`/`RATE_MAX` can go

The settings API clamps `run_stretch_limit_pct` to `[1, 50]`. Once selection enforces
`dev <= limit`, every returned rate lies in `[0.5, 1.5]` — strictly inside the old
`[0.5, 2.0]` guard. The guard becomes unreachable, and with it the server's `clamped`
flag (which was already near-dead: it measured against 0.5/2.0, not the user's limit).

## What deliberately stays

**Keep the client-side `lockRate` clamp and keep the ⚠ "stretch cap reached" badge**
(`Run.tsx` :1163–1169). Selection guarantees in-limit *at build time only*. Two things
can invalidate that afterwards:

- The target slider moves a live queue without a rebuild (`Run.tsx` :481 re-locks in
  place; the "built for X BPM — hit Rebuild" banner at :888 already covers this).
- The user lowers max stretch in Settings while a persisted queue is loaded.

In both cases the clamp is what keeps playback sane, and the ⚠ is what explains it. Its
tooltip should be reworded from "Raise the stretch limit in Settings" to something that
names the drift, e.g. *"This track was queued for a different target — rebuild the
queue."*

## Server changes (`bpm_tagger/web/api/run.py`)

```python
limit = max(0.01, float(cfg.get("run_stretch_limit_pct", 15.0)) / 100.0)

def _matches(cands, exclude_paths):
    found = []
    for t in cands:
        if t["file_path"] in exclude_paths:
            continue
        folded = _fold(t["bpm"], target, octave)
        dev = abs(target / folded - 1.0)
        if dev <= limit:
            found.append((t, folded, dev))
    found.sort(...)   # unchanged
    return found
```

`_rate` reduces to `round(target / folded, 4)`. Response gains
`stretch_limit_pct=limit * 100` in place of `tolerance_pct`.

An old cached client sending `?force=1` is harmless — the param is simply never read, so
no 400. Worth an explicit note in the PR since the PWA has no offline caching but a
stale tab can outlive a deploy.

## Frontend changes

- **Run page** — drop the force toggle everywhere; the queue header (:1082–1083) shows
  `built for {target} BPM · max ±{stretchLimitPct}%`. The three "widen the tolerance in
  Settings" empty-state strings (:500–503) become "raise **Max stretch** in Settings".
- **Settings → Run Mode** — one numeric field left. Reword its help text to carry the
  whole model now that it's alone: *"How far a track may be sped up or slowed down to
  reach your cadence. Tracks that can't get there within this limit aren't queued.
  Browser time-stretching starts to sound artificial past ~15%."*
- Consider raising the default from 15 → 20. At 15% with octave folding the eligible
  pool is already wide (worst case in-reach deviation is 33%), but 15 was chosen as a
  *playback* cap, not a *selection* cap, and it will now visibly shrink queues on small
  libraries. **Recommend keeping 15 and watching it** rather than changing two variables
  at once.

## Migration

`load_settings_override` does a blind `config.update(data)`, so a stale
`run_tolerance_pct` / `run_force_tempo` in `/data/settings.json` would keep landing in
the config dict. Nothing reads them, so it's harmless — but add a dead-key sweep so the
dict stays honest:

```python
_DEAD_SETTINGS = ("run_tolerance_pct", "run_force_tempo")
# in load_settings_override, alongside the env_locked_keys() pop:
for key in _DEAD_SETTINGS:
    data.pop(key, None)
```

The keys stay in the JSON file itself (`save_settings` only removes a key when handed an
explicit `None`). That's acceptable — it's inert and costs nothing. Purging the file
would need a one-shot `save_settings(path, {"run_tolerance_pct": None, ...})` at startup;
skip it unless the leftovers actually bother us.

`RUN_TOLERANCE_PCT` / `RUN_FORCE_TEMPO` set in a user's compose file become no-ops. They
are not validated anywhere, so a stale env var causes no error — but it must be called
out in the changelog as **breaking**, since a user who tuned tolerance down to 1% will
see a much wider pool after upgrading.

## Tests

- Delete `tests/test_force_tempo.py`.
- `tests/test_api_run.py` — the settings round-trip (:330–345) drops `run_tolerance_pct`.
  Add: a candidate outside the stretch limit is **not** returned; one inside is; the
  boundary case `dev == limit` is inclusive.
- `frontend/src/pages/Run.test.tsx` — `tolerance_pct` in the mocked response (:448) and
  the config mock (:37) become `stretch_limit_pct`. Add a case asserting the force
  toggle is gone (guards against a partial revert).
- Gates: `pytest -q`, `ruff check bpm_tagger/ tests/`, and in `frontend/`
  `npm run typecheck && npm test && npm run build`.

## Phases

1. **Backend** — `run.py` selection filter + response field, `config.py` +
   `settings.py` key removal, dead-key sweep, tests.
2. **Frontend** — Settings field removal, Run page force-toggle removal, copy rewrites,
   ⚠ tooltip rewording, tests.
3. **Docs** — README, DOCKERHUB_README, docker-compose comments, CHANGELOG (breaking
   note), `VERSION` → 2.10.0 + the README header + the CLAUDE.md Project Identity row.

Phases 1 and 2 must land together — a frontend still sending `force=1` against a new
backend is fine, but a new frontend reading `stretch_limit_pct` from an old backend gets
`undefined` in the queue header.

## Open question

Should max stretch remain a **global** setting, or move onto the Run page as a per-run
control next to the target? It is now the single knob that decides what you hear, and
"tonight I'll accept more stretch to keep the queue full" is a per-run mood, not a
permanent preference. Not in scope here — noted so it isn't re-litigated from scratch.
