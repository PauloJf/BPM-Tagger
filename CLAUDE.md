# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## README Updates

After every commit that changes user-facing behaviour, always update **both**:
- `README.md` (full documentation)
- `DOCKERHUB_README.md` (Docker Hub short description)

Include the README changes in the same commit as the code, or in an immediate follow-up commit before moving on. Never leave the session with source files committed but READMEs out of date.

---

## Commands

### Build & Run (Docker — the primary deployment path)

```bash
# Build and start (slim image, no PyTorch)
docker compose up -d --build

# Build the full image (PyTorch + deeprhythm CNN, ~1.8 GB)
docker build --build-arg WITH_DEEPRHYTHM=true -t bpm-tagger:full .

# Follow logs
docker compose logs -f

# Run a one-shot mode (e.g. lock a track)
docker compose run --rm --no-deps bpm-tagger \
  env MODE=lock LOCK_FILE="/music/artist/track.mp3" LOCK_BPM=128 \
  python bpm_tagger.py
```

### Run locally (without Docker)

```bash
pip install -r requirements.txt
pip install --pre essentia          # optional; falls back gracefully if absent

# Basic scan
MODE=scan_unscanned MUSIC_DIR=/path/to/music DB_PATH=./bpm.db python bpm_tagger.py

# With web UI
ENABLE_UI=true UI_PASSWORD=<your-password> MODE=watch MUSIC_DIR=/path/to/music python bpm_tagger.py
```

### Publish Docker images (CI)

Images are published manually via the `Publish Docker image` GitHub Actions workflow (`.github/workflows/docker-publish.yml`). It builds both the slim (`:latest`) and full (`:full`) variants from `main` using the `VERSION` file.

---

## Architecture

### Entry point and thread model

`bpm_tagger.py` is the sole entry point (`python bpm_tagger.py`). All configuration is read from environment variables and then overridden by `/data/settings.json` (persisted runtime changes from the UI). When `ENABLE_UI=true`, `web_ui.start()` is launched as a daemon thread; the main thread runs the scanner/watcher.

```
bpm_tagger.main()
  ├── loads config from env + settings.json
  ├── creates BPMTagger (owns BPMDatabase + NotificationManager)
  ├── [daemon thread] web_ui.start()  ← Flask/Waitress on :5000
  └── runs scan / watch / report / lock / unlock based on MODE
```

### BPM detection pipeline (`bpm_tagger.py`)

Every audio file passes through three detectors in order:

1. **deeprhythm** (`_detect_bpm_deeprhythm`) — PyTorch CNN; model is lazy-loaded once per worker thread via `_local` (thread-local storage). Only present in the `full` image.
2. **essentia** (`_detect_bpm_essentia`) — `RhythmExtractor2013(method="multifeature")` at 44 100 Hz; optional pre-release package, fails gracefully.
3. **librosa** (`_detect_bpm_librosa_multiseg`) — always runs; analyzes N evenly-spaced windows and returns median BPM + beat-consistency confidence score.

Results feed into `_reconcile()` which returns `(final_bpm, needs_review)`, then `_normalize_bpm()` halves/doubles the result into `[BPM_MIN, BPM_MAX]`.

### Scan phases

`BPMTagger.scan_directory()` has two distinct phases:
1. **Discovery** — walks the music directory, inserts every audio file as `status='pending'` in bulk (`bulk_register_pending`), skipping locked and already-done unchanged files. The whole library is visible in the UI immediately.
2. **Processing** — fetches all `pending` rows and runs `_process_files_parallel()` using a `ThreadPoolExecutor`. Pause/stop is controlled via `threading.Event` objects (`_pause_event`, `_stop_event`).

`file_hash` is `size:mtime` (not content hash) for speed. After writing tags, the hash is re-read to match the post-tag state, preventing a re-analysis loop on the next scan.

### Database (`BPMDatabase`)

SQLite with WAL mode. All schema migrations are additive `ALTER TABLE ADD COLUMN` statements in `_migrate()`, applied on every startup — safe to run on existing databases.

Key columns: `file_path` (unique), `file_hash`, `bpm`/`bpm_dr`/`bpm_es`/`bpm_lb`, `bpm_confidence`, `detector`, `status` (`pending`/`done`/`error`), `needs_review`, `reviewed`, `locked`, `waveform_peaks` (JSON, computed lazily and stored for the UI).

### Web UI (`web_ui.py`)

Flask app served by Waitress on `UI_PORT`. Module-level globals (`_db`, `_tagger`, `_progress`, `_config`, etc.) are populated by `start()` before the server begins accepting requests. All state-changing routes require a CSRF token (`_check_csrf()`). File paths in API calls are validated against `MUSIC_DIR` (`_assert_in_music_dir()`).

Runtime settings are saved to `/data/settings.json` via `_save_settings()` and applied in-memory immediately — no restart needed for most settings. A restart (via `/api/restart`) does `os.execv()` to replace the process in-place.

Waveform data (`/api/waveform`) has a three-level cache: in-memory dict → DB `waveform_peaks` column → on-demand librosa recompute with deduplication via `threading.Event` to prevent double-compute under concurrent requests.

### Settings persistence

`/data/settings.json` overrides env vars at startup (`load_settings_override()`). The web UI writes to this file for every settings form. This means env vars set the defaults but the UI is the source of truth after first write.

### Two Docker image variants

| Build arg | Image tag | Detectors available | Peak RAM |
|---|---|---|---|
| `WITH_DEEPRHYTHM=false` (default) | `:latest` | essentia + librosa | ~400 MB |
| `WITH_DEEPRHYTHM=true` | `:full` | deeprhythm + essentia + librosa | ~1.8 GB |

`USE_DEEPRHYTHM` env var controls whether deeprhythm is actually _used_ at runtime (must also be present in the image).

### Notifications (`NotificationManager`)

Batches outgoing ntfy pushes: fires when `NTFY_BATCH_SIZE` tracks accumulate **or** `NTFY_MIN_INTERVAL` seconds have passed since the last send. In watch mode the buffer is flushed every 60 seconds. Scan summaries and review reports are sent as separate messages.
