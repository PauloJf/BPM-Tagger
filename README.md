# BPM Tagger

```
██████╗ ██████╗ ███╗   ███╗  ████████╗ █████╗  ██████╗  ██████╗ ███████╗██████╗ 
██╔══██╗██╔══██╗████╗ ████║     ██╔══╝██╔══██╗██╔════╝ ██╔════╝ ██╔════╝██╔══██╗
██████╔╝██████╔╝██╔████╔██║     ██║   ███████║██║  ███╗██║  ███╗█████╗  ██████╔╝
██╔══██╗██╔═══╝ ██║╚██╔╝██║     ██║   ██╔══██║██║   ██║██║   ██║██╔══╝  ██╔══██╗
██████╔╝██║     ██║ ╚═╝ ██║     ██║   ██║  ██║╚██████╔╝╚██████╔╝███████╗██║  ██║
╚═════╝ ╚═╝     ╚═╝     ╚═╝     ╚═╝   ╚═╝  ╚═╝ ╚═════╝  ╚═════╝╚══════╝╚═╝  ╚═╝
                       ▁ ▂ ▃ ▅ ▇ █ ▇ ▅ ▃ ▂ ▁ ▂ ▃ ▅ ▇ ▅ ▃ ▂ ▁
           automatic bpm detection & tagging for navidrome
```

**v1.0.4** · [Changelog](CHANGELOG.md)

Automatically detects the BPM of every song in your [Navidrome](https://www.navidrome.org/) music library, writes the result back to the file's metadata tag, tracks everything in a SQLite database, sends batched [ntfy](https://ntfy.sh/) notifications, and exposes a password-protected web UI for reviewing and correcting results.

## Screenshots

| Login | Library |
|---|---|
| ![Login](docs/screenshots/01-login.png) | ![Library](docs/screenshots/02-library.png) |

| Track detail | Review queue |
|---|---|
| ![Track detail](docs/screenshots/05-track-detail.png) | ![Review](docs/screenshots/04-review.png) |

| Statistics | Settings |
|---|---|
| ![Stats](docs/screenshots/06-stats.png) | ![Settings](docs/screenshots/07-settings.png) |

| About | |
|---|---|
| ![About](docs/screenshots/08-about.png) | |

## Features

- **Three-detector BPM analysis** — [deeprhythm](https://github.com/bleugreen/deeprhythm) (CNN) and [essentia](https://essentia.upf.edu/) `RhythmExtractor2013` run as dual primary detectors; [librosa](https://librosa.org/) multi-segment analysis always runs as confidence scorer and tiebreaker
- **Octave error correction** — when any two detectors return a 2× discrepancy, the value inside your configured BPM range wins automatically
- **Plausibility normalization** — BPM is halved/doubled until it falls inside `[BPM_MIN, BPM_MAX]`
- **Parallel processing** — configurable worker thread pool; each thread maintains its own model instance for safe concurrent analysis
- **Tag writing** — writes the `BPM` tag to MP3 (ID3 `TBPM`), FLAC, OGG/Opus (Vorbis comment), M4A/AAC (MP4 `tmpo`), and any other format via mutagen
- **SQLite tracking** — records every file's path, hash, both raw detector values, final BPM, confidence, and timestamp; re-analyzes only files that are new or changed
- **Review flagging** — tracks where detectors genuinely disagree, confidence is low, or only the fallback was used are flagged `needs_review` in the DB
- **Web UI** — browser interface to browse all tracks, review flagged ones, play audio, and correct BPM with a tap-tempo button; Prev/Next navigation moves through the review queue without returning to the list
- **Login brute-force protection** — IP-based rate limiting locks out repeated failed login attempts
- **Navidrome auto-rescan** — optionally triggers a Navidrome library rescan via the Subsonic API after every scan so new BPM tags appear immediately
- **Health check endpoint** — `/healthz` returns DB statistics as JSON; no login required, suitable for Docker/k8s probes
- **ntfy notifications** — batched and rate-limited; scan summaries include a "N need review" count
- **Manual lock** — pin a track's BPM so future scans never overwrite it
- **Fully Docker-native** — all settings via environment variables in `docker-compose.yml`

---

## Quick Start

1. Clone this repository and edit `docker-compose.yml`:
   - Set `volumes` → point `/music` at your Navidrome music directory
   - Set `NTFY_TOPIC` to your ntfy topic (or leave blank to disable)
   - Set `UI_PASSWORD` if you want to enable the web UI
   - Uncomment `user:` and set it to match your Navidrome user/group if you see permission errors

2. Build and run:
   ```bash
   docker compose up -d --build
   ```

3. Follow the logs:
   ```bash
   docker compose logs -f
   ```

4. Open the web UI (if enabled):
   ```
   http://your-host:5000
   ```

---

## Hardware / Memory

Two image variants are published to Docker Hub:

| Image tag | Detectors | Peak RAM | Build |
|---|---|---|---|
| `latest` _(default)_ | essentia + librosa | ~400 MB | slim, no PyTorch |
| `full` | deeprhythm (CNN) + essentia + librosa | ~1.8 GB | with PyTorch CPU |

For the `full` image, peak RAM scales with workers:

| `WORKERS` | Peak RAM |
|---|---|
| 1 | ~870 MB |
| 2 | ~1 350 MB |
| 4 | ~2 300 MB |

Set `deploy.resources.limits.memory` in `docker-compose.yml` to at least the peak RAM for your configuration. The defaults (`latest` image, limit `800M`) are sized for NAS devices. Switch to `:full` and raise the limit if you want CNN accuracy.

---

## Operating Modes

Set the `MODE` environment variable to one of the following:

| Mode | Description |
|---|---|
| `watch` | Scans all unscanned/changed files on startup, then watches the music directory for new or modified files using filesystem events. A 10-second debounce prevents processing files still being copied. **Recommended default.** |
| `watch_all` | Re-analyzes every file on startup (overwriting existing results), then enters watch mode for new/changed files. Use when you want a full re-analysis pass followed by continuous watching. |
| `scan_all` | One-shot: re-analyze every audio file, overwriting all existing results regardless of whether the file has changed |
| `scan_unscanned` | One-shot: only analyze files not yet in the database, files whose content has changed (detected via size+mtime hash), and files with `status='error'`. Locked tracks are always skipped. |
| `scan_review` | One-shot: re-analyze only tracks that are flagged for review (`needs_review=1`), have `status='error'`, or were only analyzed by the librosa fallback. Useful for a quick follow-up pass after you've tuned detection settings. |
| `report` | Queries the database for suspicious tracks (detector disagreement, low confidence, fallback-only, out-of-range BPM), logs them, writes a CSV to `REPORT_PATH`, and sends an ntfy summary if configured. Does not analyze any files. |
| `lock` | Locks a single track so it is never re-analyzed. Requires `LOCK_FILE` (absolute path inside the container). Optionally provide `LOCK_BPM` to set a corrected BPM value and write it to the file tag at the same time. |
| `unlock` | Removes the lock from a track so it will be re-analyzed on the next scan. Requires `UNLOCK_FILE` (absolute path inside the container). |

### Lock / Unlock Examples

```bash
# Lock a track and correct its BPM to 128
docker compose run --rm --no-deps bpm-tagger \
  env MODE=lock LOCK_FILE="/music/Artist/Album/track.mp3" LOCK_BPM=128 \
  python bpm_tagger.py

# Lock a track at its current BPM (just prevent future re-analysis)
docker compose run --rm --no-deps bpm-tagger \
  env MODE=lock LOCK_FILE="/music/Artist/Album/track.mp3" \
  python bpm_tagger.py

# Unlock a track
docker compose run --rm --no-deps bpm-tagger \
  env MODE=unlock UNLOCK_FILE="/music/Artist/Album/track.mp3" \
  python bpm_tagger.py
```

---

## Configuration Reference

All settings are environment variables. Every variable has a default and is documented with a comment in `docker-compose.yml`.

### Core

| Variable | Default | Description |
|---|---|---|
| `MODE` | `watch` | Operating mode (see above) |
| `MUSIC_DIR` | `/music` | Path to the music directory inside the container |
| `DB_PATH` | `/data/bpm_tagger.db` | SQLite database path inside the container |
| `WRITE_TAGS` | `true` | Write the detected BPM back to each audio file's metadata tag |
| `AUDIO_EXTENSIONS` | `.mp3,.flac,.ogg,.m4a,.aac,.wav,.opus,.wv` | Comma-separated list of file extensions to process |
| `WORKERS` | `1` | Number of parallel worker threads for BPM analysis. Each worker loads its own deeprhythm model instance (~500 MB RAM each). Keep at `1` on NAS/low-memory devices; raise to `2`–`4` on a server with ample RAM. |
| `REFRESH_HASHES` | `false` | Before the scan starts, recompute the stored `size:mtime` hash for every already-analyzed track. Set to `true` after upgrading from a version that saved the pre-tag hash (causing every tagged file to be re-analyzed on every restart). Safe to leave enabled permanently — it adds a few seconds on large libraries but never triggers re-analysis by itself. |
| `LOG_LEVEL` | `INFO` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

### BPM Detection Quality

| Variable | Default | Description |
|---|---|---|
| `BPM_MIN` | `60` | Plausibility floor — BPM values below this are doubled until in range. Narrow to `90` for pop/rock; widen to `40` for very slow music. |
| `BPM_MAX` | `200` | Plausibility ceiling — BPM values above this are halved until in range. Raise to `220` for fast electronic music. |
| `OCTAVE_CORRECTION` | `true` | When any two detectors return a value that is approximately double/half the other, pick the one inside `[BPM_MIN, BPM_MAX]`. Fixes the most common class of detection errors. |
| `USE_DEEPRHYTHM` | `true` | Enable deeprhythm (PyTorch CNN) as a primary detector. Set to `false` on low-memory devices (NAS, small VMs) — essentia + librosa still run without loading PyTorch, saving ~500 MB RAM per worker. |
| `USE_ESSENTIA` | `true` | Enable essentia `RhythmExtractor2013` as the second primary detector. Set to `false` to revert to librosa-only mode. |
| `MULTI_SEGMENT` | `true` | Run librosa on N evenly-spaced windows across the track instead of a single 180s block. Reduces the influence of quiet intros and outros. |
| `MULTI_SEGMENT_COUNT` | `3` | Number of windows for multi-segment librosa analysis |
| `SEGMENT_DURATION` | `45` | Duration in seconds of each analysis window |

### Review & Flagging

| Variable | Default | Description |
|---|---|---|
| `REVIEW_CONFIDENCE_THRESHOLD` | `0.4` | Tracks with a librosa confidence score below this value are flagged `needs_review` in the database |
| `REVIEW_DISAGREE_THRESHOLD` | `15` | BPM difference between the two primary detectors (after octave correction) that triggers a `needs_review` flag |
| `REPORT_PATH` | `/data/review_report.csv` | Path where `MODE=report` writes the CSV export of suspicious tracks |

### ntfy Notifications

| Variable | Default | Description |
|---|---|---|
| `NTFY_URL` | `https://ntfy.sh` | ntfy server base URL. Leave `NTFY_URL` or `NTFY_TOPIC` empty to disable all notifications. |
| `NTFY_TOPIC` | _(empty)_ | ntfy topic name. Leave empty to disable. |
| `NTFY_BATCH_SIZE` | `10` | Maximum number of tracks to include in a single tagging notification |
| `NTFY_MIN_INTERVAL` | `300` | Minimum seconds between batched tagging notifications (anti-spam) |
| `NTFY_NOTIFY_REVIEW` | `true` | Include a "N need review" count in the scan-complete summary notification |

### Navidrome Auto-Rescan

| Variable | Default | Description |
|---|---|---|
| `NAVIDROME_URL` | _(empty)_ | Base URL of your Navidrome instance, e.g. `http://navidrome:4533`. Leave empty to disable. |
| `NAVIDROME_USER` | _(empty)_ | Navidrome admin username |
| `NAVIDROME_PASS` | _(empty)_ | Navidrome admin password |

When all three are set, BPM Tagger calls Navidrome's Subsonic-compatible `/rest/startScan` endpoint at the end of every `scan_all`, `scan_unscanned`, and `scan_review` run. This triggers a Navidrome library rescan automatically so the new BPM tags appear in your music player without a manual rescan step.

### Web UI

| Variable | Default | Description |
|---|---|---|
| `ENABLE_UI` | `false` | Set to `true` to start the web interface. Runs as a thread inside the same container alongside the scanner/watcher. |
| `UI_PORT` | `5000` | Port the web UI listens on inside the container |
| `UI_PASSWORD` | _(empty)_ | **Required** — password for the web UI login page. The UI will not start if this is blank. |
| `UI_SECRET_KEY` | _(empty)_ | Flask session secret key. Auto-generates a random key if empty — sessions are invalidated on each container restart. Set explicitly to persist sessions across restarts. Generate one with: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `UI_SESSION_HOURS` | `24` | How long a browser session stays valid after login |
| `UI_MAX_LOGIN_ATTEMPTS` | `5` | Number of failed login attempts allowed per IP within a 60-second window before that IP is locked out |
| `UI_LOCKOUT_SECONDS` | `300` | How long (in seconds) a locked-out IP must wait before login is re-enabled |

---

## Web UI

Enable the web UI by setting `ENABLE_UI: "true"` and a strong `UI_PASSWORD` in `docker-compose.yml`, then open `http://your-host:5000`.

### Navbar scan controls

The navigation bar shows the current scan state at all times and lets you control the scanner without touching the container:

- **Stopped** (red dot) — no scan running; **▶ Start Scan** button triggers a pass using the configured mode
- **Analysing** (pulsing green dot) — scan in progress; **⏸ Pause** and **■ Stop** buttons are shown
- **Stopping…** (pulsing red dot) — stop requested; the current track is finishing before the scan exits
- **Paused** (yellow dot) — scan is suspended; **▶ Resume** resumes from where it left off

Pausing and stopping both take effect between tracks, not mid-file, so the current track always completes cleanly.

### Settings (`/settings`)

All settings can be changed at runtime — no container restart required. Changes are saved to `/data/settings.json` and survive restarts. Configurable from the UI:

- **Password** — change the web UI login password
- **Notifications** — ntfy server URL, topic, batch size, interval, and whether to include review counts
- **Scan behavior** — worker count, detector toggles (deeprhythm, essentia), tag writing, BPM range, review confidence threshold
- **Operating mode** — controls both container startup behaviour and what **▶ Start Scan** does: `watch`/`scan_unscanned` scan new/changed files; `watch_all`/`scan_all` re-analyze everything; `scan_review` re-runs flagged and error tracks; `report` writes a CSV with no analysis
- **Navidrome integration** — URL, username, and password for auto-rescan
- **Version** — shows the current version with a **Check for latest** button that queries GitHub releases
- **Restart** — restarts the application process in-place (re-reads env vars and `settings.json`); any active scan is stopped first; the page reconnects automatically

### Stats (`/stats`)

Summary statistics and charts for your library:

- Total / analyzed / needs review / errors / locked / unscanned track counts
- Mean, median, min, and max BPM across analyzed tracks
- **BPM histogram** — bar chart showing track distribution across 5-BPM buckets
- **Detector breakdown** — share of tracks analyzed by each detector combination

### Pages

#### All Tracks (`/tracks`)
Paginated table of every analyzed track, sorted by most-recently analyzed. Columns show filename, parent folder (artist/album), BPM, confidence bar, detector used, and status badge. A per-page dropdown lets you show 10, 50, or 100 rows (default 50). A search box filters by filename. Filter pills at the top let you view **All**, **Review** (needs human check), or **Locked** tracks; live counts update automatically during a scan.

#### Needs Review (`/review`)
Filtered view showing only tracks that meet one or more of these criteria:
- `needs_review = 1` — the primary detectors disagreed beyond the threshold
- Librosa confidence below `REVIEW_CONFIDENCE_THRESHOLD`
- Only the fallback detector (`librosa`) was used
- BPM is outside `[BPM_MIN, BPM_MAX]` after normalization
- `status = 'error'`

The raw `bpm_dr` (deeprhythm), `bpm_es` (essentia), and `bpm_lb` (librosa) values are shown so you can see exactly what each detector returned.

#### Track Detail (`/track`)
Full detail page for a single track with:

- **Audio player** — streams the file directly from the container; real waveform visualization with click/drag scrubbing. Waveform peaks are computed during BPM analysis (while the audio is already in the OS page cache) and stored in the database, so the track detail page loads them instantly. Tracks processed before this version have their waveform computed on the first visit and back-filled into the DB automatically.
- **BPM metadata** — current final BPM, raw deeprhythm, essentia, and librosa results, confidence score, detector used, and status badges
- **Prev / Next navigation** — when arriving from the review queue, a navigation bar at the top shows your position (e.g. `3 / 47`) and lets you step through the queue without returning to the list. Saving or skipping a track and clicking Next moves you to the next flagged track in order.
- **Tap-tempo** — tap the large TAP button (or press **Space**) to the beat while the track is playing. The app keeps the last 8 intervals and shows a live BPM estimate that updates on every tap. Press **Apply** to copy the tap BPM into the edit field, then **Save & Lock** to write it to the file tag and lock the DB record.
- **Save & Lock** — manually enter any BPM value, click Save & Lock to write the tag and prevent future scans from overwriting it
- **Unlock** — removes the lock so the track is re-analyzed on the next scan

#### Health Check (`/healthz`)
A lightweight JSON endpoint with no authentication required, suitable for Docker or Kubernetes probes:

```json
{"status": "ok", "total": 3124, "done": 3118, "errors": 4, "needs_review": 12, "locked": 8}
```

To wire it up as a Docker health check, uncomment the `healthcheck` block in `docker-compose.yml`.

### Tap-Tempo Workflow

1. Open a track from the review queue
2. Press play on the audio player
3. Tap **TAP** (or hold **Space**) to the beat — the live BPM appears after the second tap
4. When the reading stabilizes, click **Apply →** to copy it to the input field
5. Click **Save & Lock** — the tag is written immediately and the track is locked

The tap timer resets automatically after 3 seconds of silence so you can restart a count without clicking Reset.

---

## BPM Detection Pipeline

Every file goes through this pipeline:

```
deeprhythm (CNN) ────────────────────────────┐
                                              ├─► reconcile ─► normalize ─► final BPM
essentia RhythmExtractor2013 ────────────────┤
                                              │
librosa multi-segment (confidence/tiebreaker)─┘
```

1. **deeprhythm** — a CNN trained on HCQM (Harmonic Constant-Q Modulation) features. Loads its pre-trained weights on first use per worker thread; fast at inference time.
2. **essentia** — calls `RhythmExtractor2013(method="multifeature")` at 44 100 Hz. A DSP multifeature beat tracker from the MTG Barcelona research group; gives an independent second opinion with no shared assumptions with deeprhythm.
3. **librosa** — always runs regardless of the above. In multi-segment mode N evenly-spaced windows are analyzed and the median BPM and a beat-consistency confidence score are returned. Used as confidence metric and as tiebreaker when the primary detectors disagree.
4. **Reconciliation** — the three values are compared:
   - deeprhythm and essentia agree (within `REVIEW_DISAGREE_THRESHOLD`) → average of the two, no review flag
   - Ratio ≈ 2.0 between any pair → octave correction picks the value inside `[BPM_MIN, BPM_MAX]`
   - Primary detectors disagree → librosa acts as tiebreaker; the winner is chosen but the track is flagged `needs_review`
   - One primary detector failed → remaining one is reconciled against librosa using the same rules
   - Both primary detectors failed → librosa result used alone, track flagged `needs_review`
5. **Normalization** — the chosen BPM is halved/doubled until it lands inside `[BPM_MIN, BPM_MAX]`

### Detector strings stored in the database

| `detector` value | Meaning |
|---|---|
| `deeprhythm+essentia` | Both primary detectors succeeded and agreed (or octave-corrected) |
| `deeprhythm+librosa` | essentia failed; deeprhythm reconciled against librosa |
| `essentia+librosa` | deeprhythm failed; essentia reconciled against librosa |
| `librosa` | Both primary detectors failed; librosa fallback only |

---

## Library Evaluation

Six Python BPM detection libraries were considered before choosing this stack. The goal was maximum accuracy for a diverse music library with clean Docker deployment and active maintenance.

| Library | Algorithm | ~Accuracy | Last release | pip install | Verdict |
|---|---|---|---|---|---|
| **deeprhythm** | CNN (HCQM features) | Excellent | Dec 2024 | Clean | ✅ Primary detector |
| **essentia** | DSP multifeature | ~70 % | Sep 2024 | Clean wheels | ✅ Second primary detector |
| **librosa** | DSP (onset strength) | ~71 % | Mar 2025 | Clean | ✅ Confidence + tiebreaker |
| **madmom** | RNN beat tracking | ~71 % | Nov 2017 | Needs C compiler | ❌ Stale, build fragile |
| **TempoCNN** | CNN | Very good | Oct 2024 | Pulls TensorFlow (~500 MB) | ❌ Too heavy |
| **aubio** | Causal beat tracking | OK | Feb 2019 | Needs system libaubio | ❌ Dead project, 2× BPM errors |

### Why deeprhythm

- Fast CNN inference using HCQM features — designed specifically for tempo estimation in modern music
- Lightweight: PyTorch only, ~5 MB weight file downloaded once on first run
- `DeepRhythmPredictor.predict(filename)` is a single-line call
- No system-level dependencies beyond PyTorch

### Why essentia

- Produced by the Music Technology Group (MTG) at Universitat Pompeu Fabra — one of the leading MIR research labs; `RhythmExtractor2013(method="multifeature")` is a well-validated algorithm with a long publication record
- Completely independent algorithmic approach from deeprhythm (no shared CNN, no shared feature extraction) — when both agree, confidence is genuinely high
- Returns confidence-per-beat, making it a natural complement to librosa's beat-consistency score
- Active maintenance (September 2024 beta release), pre-built manylinux wheels on PyPI — `pip install essentia` works cleanly inside Docker on Linux x86_64
- Requires 44 100 Hz audio — handled automatically via `EasyLoader`

### Why librosa (kept as third detector)

- Already present in the stack for multi-segment confidence scoring
- Pure Python after install — always available even if the other two fail
- Its multi-segment median approach is resilient to quiet intros/outros
- Serves as an independent tiebreaker when deeprhythm and essentia disagree

### Why madmom was rejected

madmom has excellent academic benchmarks (RNN beat tracking, consistently top-ranked in MIREX evaluations) but has not had a release since November 2017. It requires Cython and a C++ compiler at install time, making Docker builds fragile. Its license (CC BY-NC-SA for the pre-trained models) also restricts commercial use.

### Why TempoCNN was rejected

TempoCNN is a modern CNN alternative with a clean pip install, but it pulls in TensorFlow as a dependency (~500 MB image size increase). That overhead is unjustified when deeprhythm already provides the CNN perspective with only PyTorch.

### Why aubio was rejected

aubio has not been released since February 2019, requires `libaubio` system packages, and is known to return BPM values that are double or half the true tempo without reliable correction.

---

## Supported Formats & Tag Fields

| Extension | Tag written |
|---|---|
| `.mp3` | ID3 `TBPM` frame |
| `.flac` | Vorbis comment `BPM` |
| `.ogg`, `.opus` | Vorbis comment `BPM` |
| `.m4a`, `.aac` | MP4 atom `tmpo` (integer) |
| `.wav`, `.wv`, others | Generic mutagen `BPM` tag |

---

## SQLite Database

Default path: `/data/bpm_tagger.db`. Mount the `/data` volume to persist it across container restarts.

### Schema

| Column | Type | Description |
|---|---|---|
| `file_path` | TEXT | Absolute path to the audio file (unique key) |
| `file_hash` | TEXT | `size:mtime` fingerprint — used to detect file changes without hashing content |
| `bpm` | REAL | Final reconciled and normalized BPM (1 decimal place) |
| `bpm_dr` | REAL | Raw deeprhythm result before reconciliation (`NULL` if deeprhythm failed) |
| `bpm_es` | REAL | Raw essentia result before reconciliation (`NULL` if essentia failed or disabled) |
| `bpm_lb` | REAL | Raw librosa result before reconciliation |
| `bpm_confidence` | REAL | librosa beat-interval consistency score (0–1) |
| `detector` | TEXT | `deeprhythm+essentia`, `deeprhythm+librosa`, `essentia+librosa`, or `librosa` |
| `analyzed_at` | TEXT | ISO-8601 UTC timestamp of last analysis |
| `status` | TEXT | `done`, `error`, or `pending` |
| `needs_review` | INTEGER | `1` if flagged for manual review, `0` otherwise |
| `locked` | INTEGER | `1` if manually locked (never re-analyzed), `0` otherwise |
| `error_message` | TEXT | Exception detail when `status = 'error'` |

### Useful Queries

```sql
-- All tracks that need review
SELECT file_path, bpm, bpm_dr, bpm_es, bpm_lb, bpm_confidence, detector
FROM tracks WHERE needs_review = 1 AND status = 'done';

-- Tracks where only the fallback detector worked (both primary detectors failed)
SELECT file_path, bpm, bpm_confidence FROM tracks WHERE detector = 'librosa';

-- Tracks where the two primary detectors disagreed (tiebreaker was used)
SELECT file_path, bpm, bpm_dr, bpm_es, bpm_lb
FROM tracks WHERE needs_review = 1 AND detector = 'deeprhythm+essentia';

-- Tracks with errors
SELECT file_path, error_message FROM tracks WHERE status = 'error';

-- Locked tracks
SELECT file_path, bpm FROM tracks WHERE locked = 1;

-- Summary statistics
SELECT
  COUNT(*) AS total,
  COUNT(CASE WHEN status='done'   THEN 1 END) AS done,
  COUNT(CASE WHEN status='error'  THEN 1 END) AS errors,
  COUNT(CASE WHEN needs_review=1  THEN 1 END) AS needs_review,
  COUNT(CASE WHEN locked=1        THEN 1 END) AS locked,
  ROUND(AVG(bpm), 1) AS avg_bpm
FROM tracks;
```

---

## ntfy Notifications

Notifications are batched to avoid flooding your channel:

- A batch message fires when `NTFY_BATCH_SIZE` tracks accumulate **or** `NTFY_MIN_INTERVAL` seconds have elapsed since the last send — whichever comes first
- At the end of every scan a single summary is sent: *"Scan complete — 142 tagged, 5 need review, 0 errors (312 total in DB)"*
- In watch mode the buffer flushes every 60 seconds
- `MODE=report` sends a dedicated `⚠️ warning`-priority message listing the suspicious tracks
- Leave `NTFY_URL` or `NTFY_TOPIC` empty to disable all notifications

---

## Security

### Web UI hardening

The web UI implements multiple layers of protection:

- **CSRF protection** — every state-changing request requires a per-session CSRF token. Forms include a hidden `csrf_token` field; JavaScript API calls include an `X-CSRF-Token` header. Requests without a valid token are rejected with HTTP 403.
- **Login brute-force protection** — failed login attempts are tracked per source IP. After `UI_MAX_LOGIN_ATTEMPTS` failures within 60 seconds, that IP is locked out for `UI_LOCKOUT_SECONDS`. Counts reset only after the full lockout period expires.
- **Open redirect prevention** — the `?next=` parameter accepted after login is validated to ensure it points to this host only; external URLs are silently ignored.
- **Path traversal prevention** — the Save BPM and Unlock API endpoints validate that the supplied file path resolves within `MUSIC_DIR` before touching any file or database record.
- **SameSite=Lax cookie** — session cookies are set with `SameSite=Lax` and `HttpOnly`, blocking cross-site POST forgery and JavaScript cookie theft.
- **Security response headers** — every response includes `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, and a `Content-Security-Policy` that restricts resource loading to the same origin.
- **Production WSGI server** — the UI runs on [Waitress](https://docs.pylonsproject.org/projects/waitress/) rather than Flask's development server, providing proper threading, signal handling, and no debug-mode risk.
- **Logout via POST** — the logout button submits a POST form with a CSRF token, preventing one-click logout attacks from external pages.

### Recommendations for production

- Set a strong, unique `UI_PASSWORD` — the UI will refuse to start if this is blank.
- Set `UI_SECRET_KEY` to a stable random value so sessions survive container restarts:
  ```bash
  python -c "import secrets; print(secrets.token_hex(32))"
  ```
- If you don't need external access to the UI, bind the port to localhost in `docker-compose.yml`:
  ```yaml
  ports:
    - "127.0.0.1:5000:5000"
  ```
- Put a reverse proxy (nginx, Caddy) with TLS in front of port 5000 before exposing it to the internet.
- The Navidrome password is transmitted using Subsonic token authentication (MD5 hash + random salt) rather than as plaintext in the URL, keeping it out of server access logs.

---

## Navidrome Integration

```yaml
services:
  navidrome:
    image: deluan/navidrome:latest
    volumes:
      - /srv/music:/music:ro   # read-only for Navidrome

  bpm-tagger:
    build: ./BPM-Tagger
    environment:
      MODE: watch
      MUSIC_DIR: /music
      NTFY_TOPIC: my-music-alerts
      ENABLE_UI: "true"
      UI_PASSWORD: "your-secret-password"
    volumes:
      - /srv/music:/music      # read-write for tag writing
      - bpm_tagger_data:/data
    ports:
      - "5000:5000"
    user: "1000:1000"          # match Navidrome's UID:GID

volumes:
  bpm_tagger_data:
```

**Tips:**
- Set `user:` in `docker-compose.yml` to match Navidrome's user/group so both containers can read and write the same files without permission conflicts
- Set `NAVIDROME_URL`, `NAVIDROME_USER`, and `NAVIDROME_PASS` to trigger an automatic library rescan after every scan, so new BPM tags appear in Navidrome immediately without a manual *Administration → Rescan Library* step
- Use `MODE=watch` (the default) so newly added albums are tagged automatically within seconds of being added to the library; it scans all unprocessed files on startup before entering watch mode
- After adjusting detection settings, run `MODE=scan_review` to re-analyze only the flagged and error tracks instead of the full library

---

See [CHANGELOG.md](CHANGELOG.md) for the full release history.
