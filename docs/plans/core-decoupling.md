# Plan: Decouple the core from the optional layers

> Current status of all plans is tracked in [STATUS.md](STATUS.md).

Status: **implemented** (Unreleased, 2026-09-23). Implementation notes:

- Open questions resolved as proposed: `COMPUTE_WAVEFORMS` defaults to `auto`,
  and the non-core importers stay on the `grabber.matching` re-export.
- The layering guard also blocks the optional *packages* (flask, requests,
  rapidfuzz, …) at module level, not just the optional package modules.
- The core-only CI job needs `tests/conftest.py` to skip collecting unmarked
  test files when the optional packages are missing. pytest imports every
  module before `-m core` deselects anything.
- The **Fill missing waveforms** control only appears in Settings → Scan when
  some tracks are missing peaks.
- `pytest.mark.core` files: tags, reconcile, normalize_bpm, audio_load,
  multiseg_windows, deeprhythm_detector, config, tag_index, norm_artist_backfill,
  waveform_toggle, core_isolation. Verified in a clean venv with only
  `requirements-core.txt` (79 passed).

## Goal

The core of BPM Tagger is **BPM detection + tag writing** (+ the SQLite record
of it). Make that true at every level, not only at runtime:

1. **Runtime:** a headless run does no work that only an optional feature needs.
2. **Import:** the core modules import without any optional feature's packages
   installed.
3. **Packaging:** the core has its own dependency list, so "core only" is an
   installable, CI-tested configuration.

There should be **no user-visible behaviour change** for anyone running the
Docker image. It still installs everything, and every default stays the same
except the waveform default, which becomes "follow `ENABLE_UI`" (see A).

### What counts as core

| Core | Optional layer |
|---|---|
| `bpm/` (`audio`, `detectors`, `pipeline`, `tags`, `loudness`) | `web/` (UI, players, API) |
| `scan/` (`scanner`, `watcher`) | `grabber/` |
| `db/` (schema + `tracks` mixin; other mixins are inert tables) | `integrations/` (Navidrome, MusicBrainz, Deezer, lrclib, …) |
| `config.py`, `main.py` (dispatch) | `notify/` (ntfy), `install_ping.py` |

`bpm/waveform.py` is a UI helper that happens to live in `bpm/`. It stays there,
but the scanner stops calling it unconditionally.

Loudness stays core-adjacent. It has its own toggle (`MEASURE_LOUDNESS`), a
back-fill job (`web/api/loudness.py`), and `pyloudnorm` is already imported
lazily and degrades gracefully. No change is needed.

## Current couplings (audit 2026-09-23)

**A. Waveform peaks are computed unconditionally.**
`scan/scanner.py` (`_process_file`) calls `compute_waveform_peaks(file_path)` for
every analyzed track. That is a second full decode per track, and it has no
toggle. The only consumers are the UI's `/api/waveform` endpoint
(`web/api/tracks.py`) → `PlayerBar`, `Run`, `TrackDetail` and `TrackCompare`.
That endpoint already has a fallback chain: memory cache → DB → on-demand
recompute, deduplicated per path. So a NULL in the DB is already handled.

**B. The core imports integration code at module top.**
- `scan/scanner.py` and `scan/watcher.py` do
  `from ..integrations.navidrome import _trigger_navidrome_rescan`, which brings
  in `requests` and `grabber.matching` (→ `rapidfuzz`).
- `scan/scanner.py` also imports `notify.ntfy` (→ `requests`) at top, although
  `NotificationManager` is only built when ntfy is configured.
- **`db/tracks.py` imports `grabber.matching` at top** (for
  `normalize_artist_name` and `split_artist_credits`), and `db/base.py` imports
  it lazily in migrations. **So even the database layer can't import without
  `rapidfuzz`.** The functions it needs are pure stdlib (`re` and
  `unicodedata`). Only `score()` and `library_match()` use `rapidfuzz`.
- `index_tags()` in the scanner imports `normalize_artist` and `normalize_title`
  from `grabber.matching`.
- `main.py` imports `install_ping` (→ `requests`) at top.
- `bpm_tagger/__init__.py` eagerly re-exports `integrations.navidrome`,
  `notify.ntfy` and `main`, so a plain `import bpm_tagger` pulls in all of the
  above.

**C. There is a single `requirements.txt`.**
Flask, Waitress, rapidfuzz, yt-dlp, streamrip, pillow and requests are always
installed. The core needs only `librosa`, `numpy`, `mutagen`, `soundfile` and
`watchdog` (plus `pyloudnorm` for loudness). Essentia and deeprhythm are already
installed separately in the Dockerfile.

## Plan

### A — Waveforms only when something will use them

1. New config key `compute_waveforms` (env `COMPUTE_WAVEFORMS`). It's
   **tri-state**: `auto` (default), `true` or `false`. `auto` resolves to the
   value of `enable_ui`.
   - Default Docker/compose setups (UI on) are unchanged.
   - Headless runs (`ENABLE_UI=false`, one-shot `scan_*` modes) skip the second
     decode.
   - `true` stays available for someone who scans headless now and will serve the
     UI later from the same DB.
2. `scanner.py`: gate the `compute_waveform_peaks` call on the resolved flag.
   Leave `waveform_peaks=None` otherwise. The upsert already uses
   `COALESCE(excluded.waveform_peaks, waveform_peaks)`, so existing peaks are
   never wiped.
3. **Back-fill job**, the same shape as the loudness fill:
   `POST /api/waveform/fill/start|cancel`, `GET /api/waveform/fill/status`. It's
   single-threaded, capped per pass, and walks `waveform_peaks IS NULL AND status
   = 'done'`. A Settings button sits next to "Measure loudness for existing
   tracks". The on-demand endpoint already covers individual tracks, so this is
   only for people who want every track ready before they play it.
4. Settings UI: expose the toggle (Auto / Always / Never) under Scanning.
5. Tests:
   - A scan with `enable_ui=False` stores NULL peaks and never calls
     `compute_waveform_peaks` (monkeypatched to raise).
   - A scan with `enable_ui=True` stores peaks, which is today's behaviour.
   - `COMPUTE_WAVEFORMS=true` overrides `auto`.
   - The back-fill job fills NULL rows and skips filled ones.

*Nice-to-have, out of scope:* compute the peaks from the audio buffer that
detection already decoded, instead of decoding a second time. That needs the
pipeline to hand the buffer back, and it's a separate performance change.

### B — Core modules import without optional packages

1. **Move the pure text normalizers to a neutral module**
   `bpm_tagger/text.py` (stdlib only): `_strip_diacritics`, `_base_normalize`,
   `extract_feat`, `normalize_title`, `split_artist_credits`,
   `normalize_artist_name`, `normalize_artist`. `grabber/matching.py` keeps
   `score`, `library_match` and the `rapidfuzz` import, and **re-exports** the
   moved names (`from ..text import ...`). That way the ~20 existing importers
   and the tests keep working untouched. Then switch the core importers to
   `..text`:
   - `db/tracks.py`, `db/base.py`, `db/grabber.py`, `db/suggestions.py`
   - `scan/scanner.py` (`index_tags`)

   Non-core importers (`grabber/*`, `integrations/*`, `web/*`) can move to
   `text` opportunistically, but they don't have to.
2. **One neutral "library changed" hook** instead of the scanner knowing about
   Navidrome. Add `bpm_tagger/hooks.py`:

   ```python
   def library_changed(config: dict, full: bool = False) -> None:
       """Tell downstream servers the files changed. No-op unless one is configured."""
       if not (config.get("navidrome_url") and config.get("navidrome_user")
               and config.get("navidrome_pass")):
           return
       from .integrations.navidrome import _trigger_navidrome_rescan
       _trigger_navidrome_rescan(config, full=full)
   ```

   `scanner.py` and `watcher.py` call `hooks.library_changed(...)`. It's the
   single place a future downstream (e.g. the Subsonic albums index refresh)
   plugs in without touching `scan/`. Update the monkeypatch target in
   `tests/test_navidrome_rescan.py` (currently
   `bpm_tagger.scan.scanner._trigger_navidrome_rescan`).
3. **ntfy:** move `from ..notify.ntfy import NotificationManager` inside the
   `if config.get("ntfy_url") and config.get("ntfy_topic")` branch of
   `BPMTagger.__init__`. Type hints use a string annotation under
   `TYPE_CHECKING`.
4. **Install ping:** import `maybe_send_install_ping` inside `main()`, and make
   the import tolerant (skip with a debug log if `requests` is missing). It's
   opt-in and already a no-op without consent.
5. **Package root:** turn `bpm_tagger/__init__.py`'s eager re-exports into a
   PEP 562 lazy `__getattr__` (a name → module map). `from bpm_tagger import X`
   keeps working for every name in `__all__`, but `import bpm_tagger` no longer
   loads Navidrome, ntfy or `main` up front.
6. **Guard against regressions.** These are the tests that make "core" enforceable
   rather than a convention:
   - **Import-isolation test** (`tests/test_core_isolation.py`). In a subprocess,
     install a `sys.meta_path` finder that raises `ImportError` for `flask`,
     `waitress`, `requests`, `rapidfuzz`, `streamrip`, `yt_dlp` and `PIL`. Then
     import `bpm_tagger.scan.scanner`, `bpm_tagger.db`, `bpm_tagger.bpm.pipeline`
     and `bpm_tagger.main`, and run `BPMTagger(...).scan_directory()` on a tiny
     generated WAV (a click track at a known BPM) with the UI off and nothing
     configured. Assert that the tag was written and the row is `done`.
   - **Layering test.** An AST walk over `bpm/`, `scan/`, `db/`, `config.py` and
     `main.py` asserts that there are no **module-level** imports from `web`,
     `grabber`, `integrations`, `notify` or `install_ping`. Function-level
     (lazy) imports are allowed, which is the whole pattern.

### C — Split the dependency list

1. `requirements-core.txt`:
   `librosa`, `numpy`, `mutagen`, `soundfile`, `watchdog`, `pyloudnorm`.
2. `requirements.txt` becomes `-r requirements-core.txt` + the extras (`requests`,
   `flask`, `waitress`, `rapidfuzz`, `pillow`, `yt-dlp`, `streamrip`), with a
   comment naming which layer needs each. The Dockerfile and existing
   `pip install -r requirements.txt` instructions keep working unchanged.
3. CI (`.github/workflows/ci.yml`): add a **`core-only` job** that installs just
   `requirements-core.txt` + `pytest`, then runs
   `tests/test_core_isolation.py` and the core-marked tests
   (`pytest -m core`). Tag the existing detection, tags, pipeline and scanner test
   files with `pytestmark = pytest.mark.core` where they don't touch the web app.
   Register the marker in `pytest.ini`.
4. README: add a short "Core only (no UI)" install note under Local install:
   `pip install -r requirements-core.txt`, then `MODE=scan_unscanned`.
5. **Not in this plan:** a separate core-only Docker image, or pyproject
   extras (`pip install bpm-tagger[ui,grabber]`). The project isn't
   pip-packaged today. Revisit if someone asks for a minimal image. The saving
   would mainly be yt-dlp, streamrip and their deps, since librosa dominates the
   image size either way.

## Order and size

| Step | Size | Risk |
|---|---|---|
| B1 `text.py` move + re-exports | small, mechanical | low: re-exports keep every import path valid |
| B2–B5 hooks, lazy imports, `__init__` | small | low: one test monkeypatch target changes |
| B6 isolation + layering tests | small–medium | none (tests only) |
| C requirements split + CI job | small | low: must keep the Dockerfile install identical |
| A waveform toggle | small | low: default-on with the UI, and the endpoint already recomputes on demand |
| A3 waveform back-fill job + button | medium | low: copies the loudness-fill pattern |

Suggested order: **B → C → A**. B's tests are what make C's CI job meaningful.
A is independent and can ship alongside.

One PR, one patch release. No schema change and no breaking change. The only
changed default is waveforms off when the UI is off. Document it in the changelog
and in README/DOCKERHUB_README under the scanning settings (`COMPUTE_WAVEFORMS`).

## Open questions

1. `COMPUTE_WAVEFORMS=auto`: is "follow `ENABLE_UI`" the right default, or
   should headless users who *might* turn the UI on later get peaks anyway? The
   on-demand endpoint plus the back-fill job make `auto` safe, so I lean toward
   `auto`.
2. Should the non-core importers (`grabber`, `integrations`, `web`) be migrated
   to `bpm_tagger.text` in the same PR for tidiness, or left on the
   re-export? I lean toward leaving them: it keeps the diff small, and the
   re-export costs nothing.
