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
| `watch` | Scan all unscanned/changed files on start, then watch for new/changed files in real time. **Recommended default.** |
| `watch_all` | Re-analyze every file on start, then watch for new/changed files in real time |
| `scan_all` | One-shot: re-analyze every file (ignores existing DB results) |
| `scan_unscanned` | One-shot: analyze only new or changed files |
| `scan_review` | One-shot: re-analyze only flagged, errored, or fallback-only tracks |
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
| `WORKERS` | `1` | Parallel analysis threads (each deeprhythm worker adds ~500 MB RAM) |
| `REFRESH_HASHES` | `false` | Recompute stored hashes for all done tracks before scanning; set `true` after upgrading from a version that stored pre-tag hashes |
| `BPM_MIN` | `60` | BPM range floor (values below are doubled) |
| `BPM_MAX` | `200` | BPM range ceiling (values above are halved) |
| `USE_DEEPRHYTHM` | `false` | Enable deeprhythm CNN detector (~500 MB RAM per worker); disable on NAS/low-memory devices |
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

- **Navbar scan controls** — Start, Pause, Resume, and Stop the scanner from any page; live status shows Analysing / Stopping… / Paused / Stopped; Stop waits for the current track to finish before exiting
- Browse all tracks with BPM, confidence, and detector info; configurable rows per page (10/50/100); filter pills to view **All / Review / Locked** subsets with live counts
- **Needs Review** queue — step through flagged tracks with Prev/Next navigation
- Stream audio with a **real waveform** (scrub by clicking/dragging); use the **tap-tempo** button (or Space bar) to tap the BPM by ear
- **Save & Lock** a corrected BPM to prevent future scans from overwriting it
- **Stats page** — BPM histogram, detector breakdown, and summary statistics for your library
- **Settings page** — all settings (workers, detectors, BPM range, ntfy, Navidrome, operating mode) take effect immediately without restarting the container; the mode setting also controls what **▶ Start Scan** does; changes persist to `/data/settings.json`; a **Restart** button replaces the process in-place and reconnects the browser automatically
- `/healthz` endpoint returns DB stats as JSON — no login required

---

## Supported Formats

`.mp3` · `.flac` · `.ogg` · `.opus` · `.m4a` · `.aac` · `.wav` · `.wv`

---

## Memory Requirements

| Configuration | Peak RAM | Notes |
|---|---|---|
| `USE_DEEPRHYTHM=false` (default) | ~390 MB | essentia + librosa only; no PyTorch |
| `USE_DEEPRHYTHM=true`, `WORKERS=1` | ~870 MB | adds PyTorch CNN (~500 MB) |
| `USE_DEEPRHYTHM=true`, `WORKERS=2` | ~1 350 MB | each worker loads its own model copy |
| `USE_DEEPRHYTHM=true`, `WORKERS=4` | ~2 300 MB | only for high-RAM servers |

Set `deploy.resources.limits.memory` to at least the peak for your config. The defaults (`USE_DEEPRHYTHM=false`, limit `800M`) are sized for NAS devices. Re-enable deeprhythm for maximum accuracy if your host has enough RAM.
