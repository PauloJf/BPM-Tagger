# BPM Tagger

Auto-detects BPM for every track in your [Navidrome](https://www.navidrome.org/) library, writes it back to the file's metadata, and provides a password-protected web UI for reviewing and correcting results.

Three detectors cross-validate every track — **deeprhythm** (CNN), **essentia** RhythmExtractor2013, and **librosa** — so octave errors are caught automatically and disagreements are flagged for manual review.

Source & full docs: [github.com/PauloJf/BPM-Tagger](https://github.com/PauloJf/BPM-Tagger)

---

## Image Tags

| Tag | Detectors | Peak RAM | Use when |
|---|---|---|---|
| `latest` _(default)_ | essentia + librosa | ~400 MB | NAS / low-memory devices |
| `full` | deeprhythm (CNN) + essentia + librosa | ~1.8 GB | Servers with spare RAM |

---

## Quick Start

```yaml
services:
  bpm-tagger:
    image: gatoserio/bpm-tagger:latest   # or :full for deeprhythm CNN
    restart: unless-stopped
    environment:
      MODE: watch
      MUSIC_DIR: /music
      WRITE_TAGS: "true"
      ENABLE_UI: "false"
      UI_PORT: "5000"
      UI_PASSWORD: ""        # required if ENABLE_UI=true
      NTFY_URL: https://ntfy.sh
      NTFY_TOPIC: ""
    volumes:
      - /path/to/your/music:/music
      - bpm_tagger_data:/data
    ports:
      - "5000:5000"          # only needed if ENABLE_UI=true
    # user: "1000:1000"      # match Navidrome's UID:GID

volumes:
  bpm_tagger_data:
```

```bash
docker compose up -d && docker compose logs -f
```

---

## Operating Modes

| Mode | Description |
|---|---|
| `watch` | Scan new/changed files on start, then watch in real time. **Default.** |
| `watch_all` | Re-analyze every file on start, then watch in real time |
| `scan_all` | One-shot: re-analyze every file |
| `scan_unscanned` | One-shot: analyze only new or changed files |
| `scan_review` | One-shot: re-analyze flagged, errored, or fallback-only tracks |
| `report` | Write a CSV of suspicious tracks; send ntfy summary |
| `lock` | Lock a track's BPM (`LOCK_FILE`; optional `LOCK_BPM`) |
| `unlock` | Unlock a track for re-analysis (`UNLOCK_FILE`) |

---

## Key Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MODE` | `watch` | Operating mode |
| `MUSIC_DIR` | `/music` | Music directory inside the container |
| `WRITE_TAGS` | `true` | Write BPM to audio file metadata |
| `WORKERS` | `1` | Parallel analysis threads (+~500 MB RAM each for deeprhythm) |
| `REFRESH_HASHES` | `false` | Recompute hashes before scanning (migration from pre-1.0.0) |
| `BPM_MIN` | `60` | BPM floor — values below are doubled |
| `BPM_MAX` | `200` | BPM ceiling — values above are halved |
| `USE_DEEPRHYTHM` | `false` | CNN detector (~500 MB/worker); disable on low-RAM devices |
| `USE_ESSENTIA` | `true` | Essentia RhythmExtractor2013 detector |
| `OCTAVE_CORRECTION` | `true` | Auto-fix 2× BPM errors between detectors |
| `REVIEW_DISAGREE_THRESHOLD` | `15` | BPM gap that flags a track for review |
| `ENABLE_UI` | `false` | Start the web UI |
| `UI_PASSWORD` | _(empty)_ | Web UI password — **required** when UI is enabled |
| `NTFY_TOPIC` | _(empty)_ | ntfy topic (leave empty to disable) |
| `NAVIDROME_URL` | _(empty)_ | Trigger Navidrome rescan after each scan |

All variables documented in [`docker-compose.yml`](https://github.com/PauloJf/BPM-Tagger/blob/main/docker-compose.yml).

---

## Web UI

Set `ENABLE_UI: "true"` and a strong `UI_PASSWORD`, then open `http://your-host:5000`.

- Scan controls (Start / Pause / Resume / Stop) in the navbar; live status dot; hamburger menu on mobile (≤700 px)
- Library table with BPM, confidence, detector info, filter pills (All / Review / Locked), and `pending` badge before analysis
- Review queue — Prev/Next navigation, Approve without re-analysing
- Audio player with real waveform scrubbing and tap-tempo (Space bar)
- Save & Lock corrected BPM; Unlock for re-analysis
- Stats — BPM histogram, detector breakdown, Retry Errors
- Settings — live config changes without restart; `/healthz` JSON endpoint

---

## Supported Formats

`.mp3` · `.flac` · `.ogg` · `.opus` · `.m4a` · `.aac` · `.wav` · `.wv`

---

## Changelog

Full history: [CHANGELOG.md](https://github.com/PauloJf/BPM-Tagger/blob/main/CHANGELOG.md)

**v1.0.3** — Slim image (~400 MB, no PyTorch) as default `:latest`; new `:full` tag adds deeprhythm CNN (~1.8 GB).

**v1.0.2** — Mobile nav (hamburger menu, scroll-strip settings sidebar, scan controls on small screens).

**v1.0.1** — Two-phase scan: all files registered as `pending` before analysis; interrupted scans resume.

**v1.0.0** — First stable release: full UI redesign, real waveform, tap-tempo, CSS histogram.
