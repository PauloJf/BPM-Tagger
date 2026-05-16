# BPM Tagger

Automatically detects the BPM of every song in your [Navidrome](https://www.navidrome.org/) music library, writes the result back to the file's metadata tag, tracks everything in a SQLite database, sends batched [ntfy](https://ntfy.sh/) notifications, and exposes a password-protected web UI for reviewing and correcting results.

## Features

- **Dual-detector BPM analysis** — [deeprhythm](https://github.com/Auto-Janus/DeepRhythm) (deep learning CNN) runs first; [librosa](https://librosa.org/) multi-segment analysis always runs in parallel for cross-validation
- **Octave error correction** — when the two detectors return a 2× discrepancy, the value inside your configured BPM range wins automatically
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

## Operating Modes

Set the `MODE` environment variable to one of the following:

| Mode | Description |
|---|---|
| `scan_all` | Re-analyze every audio file, overwriting all existing results regardless of whether the file has changed |
| `scan_unscanned` | Only analyze files not yet in the database, files whose content has changed (detected via size+mtime hash), and files with `status='error'`. Locked tracks are always skipped. |
| `scan_review` | Re-analyze only tracks that are flagged for review (`needs_review=1`), have `status='error'`, or were only analyzed by the librosa fallback. Useful for a quick follow-up pass after you've tuned detection settings. |
| `watch` | Runs `scan_unscanned` on startup (if `SCAN_ON_START=true`), then watches the music directory for new or modified files using filesystem events. A 10-second debounce prevents processing files that are still being copied. |
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
| `SCAN_ON_START` | `true` | Run `scan_unscanned` before entering watch mode (watch mode only) |
| `AUDIO_EXTENSIONS` | `.mp3,.flac,.ogg,.m4a,.aac,.wav,.opus,.wv` | Comma-separated list of file extensions to process |
| `WORKERS` | `4` | Number of parallel worker threads for BPM analysis. Each worker loads its own deeprhythm model instance. Higher values increase throughput at the cost of RAM and CPU. |
| `LOG_LEVEL` | `INFO` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

### BPM Detection Quality

| Variable | Default | Description |
|---|---|---|
| `BPM_MIN` | `60` | Plausibility floor — BPM values below this are doubled until in range. Narrow to `90` for pop/rock; widen to `40` for very slow music. |
| `BPM_MAX` | `200` | Plausibility ceiling — BPM values above this are halved until in range. Raise to `220` for fast electronic music. |
| `OCTAVE_CORRECTION` | `true` | When the two detectors return a value that is approximately double/half the other, pick the one inside `[BPM_MIN, BPM_MAX]`. Fixes the most common class of detection errors. |
| `MULTI_SEGMENT` | `true` | Run librosa on N evenly-spaced windows across the track instead of a single 180s block. Reduces the influence of quiet intros and outros. |
| `MULTI_SEGMENT_COUNT` | `3` | Number of windows for multi-segment librosa analysis |
| `SEGMENT_DURATION` | `45` | Duration in seconds of each analysis window |

### Review & Flagging

| Variable | Default | Description |
|---|---|---|
| `REVIEW_CONFIDENCE_THRESHOLD` | `0.4` | Tracks with a librosa confidence score below this value are flagged `needs_review` in the database |
| `REVIEW_DISAGREE_THRESHOLD` | `15` | BPM difference between deeprhythm and librosa (after octave correction) that triggers a `needs_review` flag |
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
| `UI_PASSWORD` | `changeme` | Password for the web UI login page. **Change this before exposing port 5000 to any network.** |
| `UI_SECRET_KEY` | _(empty)_ | Flask session secret key. Auto-generates a random key if empty — sessions are invalidated on each container restart. Set explicitly to persist sessions across restarts. |
| `UI_SESSION_HOURS` | `24` | How long a browser session stays valid after login |
| `UI_MAX_LOGIN_ATTEMPTS` | `5` | Number of failed login attempts allowed per IP within a 60-second window before that IP is locked out |
| `UI_LOCKOUT_SECONDS` | `300` | How long (in seconds) a locked-out IP must wait before login is re-enabled |

---

## Web UI

Enable the web UI by setting `ENABLE_UI: "true"` and a strong `UI_PASSWORD` in `docker-compose.yml`, then open `http://your-host:5000`.

### Pages

#### All Tracks (`/tracks`)
Paginated table of every analyzed track, sorted by most-recently analyzed. Columns show filename, parent folder (artist/album), BPM, confidence bar, detector used, and status badge. A search box filters by filename. Rows flagged for review are highlighted.

#### Needs Review (`/review`)
Filtered view showing only tracks that meet one or more of these criteria:
- `needs_review = 1` — the two detectors disagreed beyond the threshold
- Librosa confidence below `REVIEW_CONFIDENCE_THRESHOLD`
- Only the fallback detector (`librosa`) was used
- BPM is outside `[BPM_MIN, BPM_MAX]` after normalization
- `status = 'error'`

The raw `bpm_dr` (deeprhythm) and `bpm_lb` (librosa) values are shown so you can see exactly what each detector returned.

#### Track Detail (`/track`)
Full detail page for a single track with:

- **Audio player** — streams the file directly from the container; supports seeking
- **BPM metadata** — current final BPM, raw deeprhythm and librosa results, confidence score, detector used, and status badges
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
deeprhythm ──────────────────────────────┐
                                          ├─► reconcile ─► normalize ─► final BPM
librosa (multi-segment or single-pass) ──┘
```

1. **deeprhythm** — loads the pre-trained CNN model (baked into the Docker image at build time; no internet needed at runtime) and returns a BPM estimate
2. **librosa** — always runs, regardless of whether deeprhythm succeeded. In multi-segment mode, N evenly-spaced windows are analyzed and the median BPM and confidence are returned
3. **Reconciliation** — if deeprhythm succeeded, the two results are compared:
   - If the ratio is ≈ 2.0 (octave error), the value inside `[BPM_MIN, BPM_MAX]` is chosen
   - Otherwise, deeprhythm wins; if the difference exceeds `REVIEW_DISAGREE_THRESHOLD`, the track is flagged `needs_review`
   - If deeprhythm failed, only the librosa result is used (`detector = 'librosa'`)
4. **Normalization** — the chosen BPM is halved/doubled until it lands inside `[BPM_MIN, BPM_MAX]`

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
| `bpm_lb` | REAL | Raw librosa result before reconciliation |
| `bpm_confidence` | REAL | librosa beat-interval consistency score (0–1) |
| `detector` | TEXT | `deeprhythm+librosa`, `librosa` |
| `analyzed_at` | TEXT | ISO-8601 UTC timestamp of last analysis |
| `status` | TEXT | `done`, `error`, or `pending` |
| `needs_review` | INTEGER | `1` if flagged for manual review, `0` otherwise |
| `locked` | INTEGER | `1` if manually locked (never re-analyzed), `0` otherwise |
| `error_message` | TEXT | Exception detail when `status = 'error'` |

### Useful Queries

```sql
-- All tracks that need review
SELECT file_path, bpm, bpm_dr, bpm_lb, bpm_confidence, detector
FROM tracks WHERE needs_review = 1 AND status = 'done';

-- Tracks that used only the fallback detector
SELECT file_path, bpm, bpm_confidence FROM tracks WHERE detector = 'librosa';

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
- Use `MODE=watch` with `SCAN_ON_START=true` (the default) so newly added albums are tagged automatically within seconds of being added to the library
- After adjusting detection settings, run `MODE=scan_review` to re-analyze only the flagged and error tracks instead of the full library
