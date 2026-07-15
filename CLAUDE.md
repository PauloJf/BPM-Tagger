# BPM Tagger — Master Project Context

## Cost Guardrail (ALWAYS ENFORCE)

Before taking any action that could incur charges on the team plan — including but not limited to: making API calls, spawning paid agents (e.g. /ultrareview), running cloud services, using billable MCP tools, or any other metered resource — always warn the user and ask for explicit confirmation before proceeding.

---

## Project Identity

| Field | Value |
|---|---|
| Name | BPM Tagger |
| Version | v2.6.1 |
| GitHub | https://github.com/PauloJf/BPM-Tagger (public since 2026-05-27) |
| Docker Hub | `gatoserio/bpm-tagger:latest` (slim) · `gatoserio/bpm-tagger:full` (PyTorch/deeprhythm) |
| Author | Paulo (paulo@gatoserio.dev) |
| Git identity | Always use `paulo@gatoserio.dev` / `Paulo` for all commits on this project |
| Origin | Built by a runner who needed BPM-tagged music for cadence-synced running |

---

## What It Does

Automatically detects the BPM of every track in a [Navidrome](https://www.navidrome.org/) music library, writes the result back to the file's metadata tag, tracks everything in a SQLite database, sends batched [ntfy](https://ntfy.sh/) notifications, and exposes a password-protected **React** web UI for reviewing and correcting results. The UI installs as a **PWA** (opens on `/run`; Media Session lock-screen controls; no offline caching by design) and includes **Run mode** — a tempo-run cadence player: octave-folded BPM queue building preferring **starred** tracks, and a pitch-preserving tempo lock via `playbackRate`.

Optional **Music Grabber** (`GRABBER_ENABLED=true`): watch your own Spotify playlists, reconcile them against the library (by ISRC or fuzzy score), download the missing tracks (Deezer via your own ARL → yt-dlp fallback), transcode to one format, tag + BPM-analyze, and file them by a path template — with an ambiguity inbox, download queue, and ntfy pings.

---

## Repository Layout

| File / Dir | Purpose |
|---|---|
| `bpm_tagger/` | Backend package — `python -m bpm_tagger` (see sub-modules below) |
| `bpm_tagger/main.py` | Entry point: build config → dispatch on `MODE` |
| `bpm_tagger/config.py` | env → config table, `settings.json` overrides, version discovery |
| `bpm_tagger/db.py` | `BPMDatabase` — SQLite, WAL, additive migrations |
| `bpm_tagger/bpm/` | Detection: `detectors.py`, `pipeline.py` (reconcile/normalize + `ScanProgress`), `tags.py`, `waveform.py` |
| `bpm_tagger/scan/` | `scanner.py` (`BPMTagger`), `watcher.py` (filesystem watch mode) |
| `bpm_tagger/grabber/` | Spotify sync + downloader: `sync_engine.py`, `worker.py`, `spotify.py`, `matching.py`, `path_template.py`, `transcode.py`, `tagging.py`, `providers/` |
| `bpm_tagger/integrations/` | `navidrome.py` (Subsonic rescan), `isrc.py`, `musicbrainz.py` |
| `bpm_tagger/notify/` | `ntfy.py` — `NotificationManager` |
| `bpm_tagger/web/` | Flask app factory (`app.py`), `auth.py` (CSRF), `state.py`, and JSON API blueprints under `api/` |
| `bpm_tagger/trash.py` | Soft-delete / recoverable trash for duplicate resolution |
| `frontend/` | React SPA (Vite + TypeScript + Tailwind); built to `frontend/dist`, served by Flask |
| `web_ui.py` | Back-compat shim → re-exports `bpm_tagger.web.app.start` / `create_app` |
| `tests/` | pytest suite (`conftest.py` + `test_*.py`); `pytest.ini` sets `pythonpath` |
| `ruff.toml` | Ruff lint config |
| `run.ps1` | Local Windows dev launcher |
| `requirements.txt` | Python dependencies |
| `Dockerfile` | Multi-stage build; `WITH_DEEPRHYTHM=true` for full image; builds the frontend bundle |
| `docker-compose.yml` | Primary deployment path |
| `.github/workflows/ci.yml` | CI on push/PR — backend (ruff + pytest) and frontend (tsc + build) |
| `.github/workflows/docker-publish.yml` | Manual CI — builds & pushes both image variants |
| `VERSION` | Single source of version truth for CI |
| `README.md` | Full user-facing documentation |
| `DOCKERHUB_README.md` | Docker Hub short description |
| `CHANGELOG.md` | Version history |
| `docs/screenshots/` | UI screenshots used in README |

---

## Architecture

### Entry Point & Thread Model

```
bpm_tagger.main()  (bpm_tagger/main.py)
  ├── build_config() (env) → load_settings_override() (/data/settings.json)
  ├── creates BPMTagger (owns BPMDatabase + NotificationManager)
  ├── if GRABBER_ENABLED: creates GrabberService (Spotify sync + download workers)
  ├── if ENABLE_UI: [daemon thread] bpm_tagger.web.app.start() ← Flask/Waitress on :5000
  └── dispatches on MODE: scan_all / scan_unscanned / scan_review / watch / watch_all / report / lock / unlock
```

### BPM Detection Pipeline

Three detectors run in order per file:

1. **deeprhythm** — PyTorch CNN; lazy-loaded per worker thread. Full image only.
2. **essentia** — `RhythmExtractor2013(method="multifeature")` at 44 100 Hz; optional, fails gracefully.
3. **librosa** — always present; multi-segment analysis, returns median BPM + beat-consistency confidence.

Detectors live in `bpm/detectors.py`; `bpm/pipeline.py` orchestrates per-file detection (`detect_bpm()`), reconciliation (`_reconcile()` → `(final_bpm, needs_review)`), and normalization (`_normalize_bpm()` into `[BPM_MIN, BPM_MAX]`), plus the shared `ScanProgress` state. Tag writing is `bpm/tags.py` (`write_bpm_tag`); waveform peaks are `bpm/waveform.py`.

### Scan Phases (`scan/scanner.py` → `BPMTagger`; watch mode in `scan/watcher.py`)

1. **Discovery** — bulk-registers all audio files as `status='pending'` (skips locked/unchanged). Library is immediately visible in UI.
2. **Processing** — `ThreadPoolExecutor` processes all `pending` rows. `_pause_event` / `_stop_event` control pause/stop.

`file_hash` = `size:mtime` (not content hash) for speed. Hash is re-read after tag write to avoid re-analysis loop.

### Grabber Subsystem (`bpm_tagger/grabber/`, opt-in via `GRABBER_ENABLED`)

`GrabberService` (`sync_engine.py`) owns Spotify sync and the download worker pool; wired in `main.py` and given to the web layer via `BPMTagger.grabber`. Flow:

1. **Sync** — `spotify.py` pulls watched-playlist tracks (Authorization Code OAuth; refresh token in the `oauth_tokens` table). `matching.py` reconciles each against the indexed library by ISRC or a fuzzy title+artist+duration score → have / missing / queued.
2. **Download** — `worker.py` (`GrabWorker`) pulls from `grab_queue`, tries providers in `PROVIDER_ORDER` (`providers/`: `deezer.py` via streamrip, `ytdlp.py`; `monochrome.py` is on hold), then `transcode.py` (ffmpeg → one `OUTPUT_FORMAT`) → `tagging.py` (tags + cover) → BPM analysis → filed via `path_template.py`. Grabbed files are marked `managed` and hash-stamped so the watcher leaves them alone.
3. **Ambiguity** — low-confidence matches wait in the inbox (`grab_candidates`) for choose / re-search / skip, with an ntfy ping. ISRC lookups use `integrations/isrc.py` (+ `musicbrainz.py`). Duplicate resolution soft-deletes to `trash.py`.

### Database (`bpm_tagger/db.py` → `BPMDatabase`)

SQLite, WAL mode. Schema migrations are additive `ALTER TABLE ADD COLUMN` in `_migrate()` — safe on existing DBs.

`tracks` key columns: `file_path`, `file_hash`, `bpm` / `bpm_dr` / `bpm_es` / `bpm_lb`, `bpm_confidence`, `detector`, `status` (`pending` / `done` / `error` / `deleted`), `needs_review`, `reviewed`, `locked`, `starred`, `disliked`, `isrc`, `managed`, `waveform_peaks` (JSON, lazy-computed). Grabber tag-index columns: `title` / `artist` / `album` / `album_artist` / `norm_artist` / `norm_title` / `duration_ms` / `spotify_track_id`.

Grabber tables: `playlists`, `playlist_tracks`, `grab_queue`, `grab_candidates` (inbox), `grab_events` (history), `oauth_tokens` (Spotify refresh token), `dismissed_dupes`.

### Web UI (`bpm_tagger/web/`, React SPA + Flask JSON API)

- **Frontend** is a React SPA (`frontend/`, Vite + TypeScript + Tailwind) built to `frontend/dist`. Flask serves the hashed bundle, static shell, and an `index.html` catch-all for client routes; `web_ui.py` is a back-compat shim.
- **Backend** is a Flask app factory (`web/app.py` → `create_app` / `start`), served by Waitress on `UI_PORT` (default 5000, 12 threads). JSON API split into blueprints under `web/api/`: `auth`, `tracks`, `scan`, `stats`, `settings`, `media`, `spotify`, `playlists`, `queue`, `inbox`, `lyrics`, `images`, `run`.
- Shared request state lives in `web/state.py` (`AppState`, on `app.extensions["state"]`) — holds `db`, `progress`, `tagger`, config, `settings_path`.
- All state-changing routes require a per-session CSRF token (`web/auth.py`; SPA fetches it from `/api/me`). File paths are validated against `MUSIC_DIR`.
- Security: `SameSite=Lax` + `HttpOnly` cookies (`Secure` when `UI_PUBLIC_URL` is https), CSP restricting to same-origin (Spotify image CDNs allowed for cover art), standard hardening headers.
- Runtime settings saved to `/data/settings.json` (`config.save_settings`) — no restart needed for most changes.
- Restart via the settings API uses `os.execv()` (in-place process replacement).
- Waveform cache: in-memory dict → DB `waveform_peaks` → on-demand librosa recompute (deduplicated via `threading.Event`).

### Settings Persistence

`/data/settings.json` overrides env vars at startup. Env vars = defaults; UI = source of truth after first write.

### Notifications (`bpm_tagger/notify/ntfy.py` → `NotificationManager`)

Batches ntfy pushes: fires on `NTFY_BATCH_SIZE` tracks accumulated **or** `NTFY_MIN_INTERVAL` seconds elapsed. Watch mode flushes every 60 s. Scan summaries and review reports are separate messages.

---

## Docker Image Variants

| Build arg | Tag | Detectors | Peak RAM |
|---|---|---|---|
| `WITH_DEEPRHYTHM=false` (default) | `:latest` | essentia + librosa | ~400 MB |
| `WITH_DEEPRHYTHM=true` | `:full` | deeprhythm + essentia + librosa | ~1.8 GB |

`USE_DEEPRHYTHM` env var controls runtime usage (detector must also be present in the image).

---

## Key Commands

### Docker (primary)

```bash
# Build and start (slim)
docker compose up -d --build

# Build full image
docker build --build-arg WITH_DEEPRHYTHM=true -t bpm-tagger:full .

# Follow logs
docker compose logs -f

# One-shot mode (e.g. lock a track)
docker compose run --rm --no-deps bpm-tagger \
  env MODE=lock LOCK_FILE="/music/artist/track.mp3" LOCK_BPM=128 \
  python -m bpm_tagger
```

### Local

```bash
pip install -r requirements.txt
pip install --pre essentia   # optional

MODE=scan_unscanned MUSIC_DIR=/path/to/music DB_PATH=./bpm.db python -m bpm_tagger
ENABLE_UI=true UI_PASSWORD=<pw> MODE=watch MUSIC_DIR=/path/to/music python -m bpm_tagger
```

On Windows, `run.ps1` wraps the local launch.

### Frontend

```bash
cd frontend
npm ci
npm run dev        # Vite dev server (proxies the API)
npm run typecheck  # tsc --noEmit
npm run build      # emits frontend/dist (what Flask serves)
```

### Tests & lint

```bash
pytest -q                    # backend suite (pytest.ini sets pythonpath)
ruff check bpm_tagger/ tests/
```

### CI & publishing

- **`ci.yml`** runs on every push to `main` / `feature/music-grabber` and on PRs: backend (ruff + pytest) and frontend (tsc + `npm run build`).
- **`docker-publish.yml`** is manually triggered — builds & pushes both image variants from `main` using the `VERSION` file.

---

## README Rules

After every commit that changes user-facing behaviour, update **both**:
- `README.md`
- `DOCKERHUB_README.md`

Include README changes in the same commit or an immediate follow-up. Never leave source files committed with READMEs out of date.

---

## Launch & Community

- Made public: 2026-05-27
- Community posts published 2026-05-27 on: r/IMadeThis, r/selfhosted, r/navidrome (comment), r/running, r/homelab
- selfh.st newsletter blurb submitted 2026-05-28
