# BPM Tagger

Automatically detects the BPM of every song in your [Navidrome](https://www.navidrome.org/) music library, writes the result back to the file's metadata tag, and exposes a password-protected web UI for reviewing and correcting results.

Three independent detectors cross-validate every track — **deeprhythm** (CNN), **essentia** RhythmExtractor2013, and **librosa** multi-segment — so octave errors are caught automatically and disagreements are flagged for manual review.

Full documentation and source: [github.com/PauloJf/BPM-Tagger](https://github.com/PauloJf/BPM-Tagger)

---

## Quick Start

```yaml
services:
  bpm-tagger:
    image: gatoserio/bpm-tagger:latest
    restart: unless-stopped
    environment:
      MODE: watch
      MUSIC_DIR: /music
      WRITE_TAGS: "true"
      # Web UI (optional)
      ENABLE_UI: "false"
      UI_PORT: "5000"
      UI_PASSWORD: ""           # required if ENABLE_UI=true
      # ntfy notifications (optional)
      NTFY_URL: https://ntfy.sh
      NTFY_TOPIC: ""
    volumes:
      - /path/to/your/music:/music
      - bpm_tagger_data:/data
    ports:
      - "5000:5000"             # only needed if ENABLE_UI=true
    # user: "1000:1000"         # uncomment to match Navidrome's UID:GID

volumes:
  bpm_tagger_data:
```

```bash
docker compose up -d
docker compose logs -f
```

---

## Operating Modes

Set `MODE` to control what the container does on startup:

| Mode | Description |
|---|---|
| `watch` | Scan unanalyzed files on start, then watch for new/changed files in real time |
| `scan_all` | Re-analyze every file (ignores existing DB results) |
| `scan_unscanned` | Analyze only new or changed files |
| `scan_review` | Re-analyze only flagged, errored, or fallback-only tracks |
| `report` | Write a CSV of suspicious tracks; send ntfy summary |
| `lock` | Lock a track's BPM (requires `LOCK_FILE`; optional `LOCK_BPM`) |
| `unlock` | Unlock a track for re-analysis (requires `UNLOCK_FILE`) |

---

## Key Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MODE` | `watch` | Operating mode (see above) |
| `MUSIC_DIR` | `/music` | Music directory path inside the container |
| `WRITE_TAGS` | `true` | Write BPM back to audio file metadata |
| `WORKERS` | `4` | Parallel analysis threads |
| `BPM_MIN` | `60` | BPM range floor (values below are doubled) |
| `BPM_MAX` | `200` | BPM range ceiling (values above are halved) |
| `USE_ESSENTIA` | `true` | Enable essentia as second primary detector |
| `OCTAVE_CORRECTION` | `true` | Auto-fix 2× BPM errors between detectors |
| `REVIEW_DISAGREE_THRESHOLD` | `15` | BPM gap between detectors that flags a track for review |
| `ENABLE_UI` | `false` | Start the web UI |
| `UI_PASSWORD` | _(empty)_ | Web UI password — **required** when UI is enabled |
| `NTFY_TOPIC` | _(empty)_ | ntfy topic for notifications (leave empty to disable) |
| `NAVIDROME_URL` | _(empty)_ | Trigger Navidrome rescan after each scan |

All variables and their defaults are documented in [`docker-compose.yml`](https://github.com/PauloJf/BPM-Tagger/blob/main/docker-compose.yml).

---

## Detection Pipeline

```
deeprhythm (CNN) ─────────────────────┐
                                       ├─► reconcile ─► normalize ─► tag
essentia RhythmExtractor2013 ─────────┤
                                       │
librosa multi-segment (tiebreaker) ───┘
```

- **deeprhythm + essentia agree** → average used, no review flag
- **Octave error (2× ratio)** → value inside BPM range is chosen automatically
- **Detectors disagree** → librosa picks the winner, track flagged for manual review
- **Detector fails** → remaining detectors cover; pure librosa as last resort

---

## Web UI

Set `ENABLE_UI: "true"` and a strong `UI_PASSWORD`, then open `http://your-host:5000`.

- Browse all tracks with BPM, confidence, and detector info
- **Needs Review** queue — step through flagged tracks with Prev/Next navigation
- Stream audio and use the **tap-tempo** button (or Space bar) to tap the BPM by ear
- **Save & Lock** a corrected BPM to prevent future scans from overwriting it
- `/healthz` endpoint returns DB stats as JSON — no login required

---

## Supported Formats

`.mp3` · `.flac` · `.ogg` · `.opus` · `.m4a` · `.aac` · `.wav` · `.wv`
