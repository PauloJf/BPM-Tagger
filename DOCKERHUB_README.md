# BPM Tagger

Auto-detects BPM for every track in your [Navidrome](https://www.navidrome.org/) library, writes it back to the file's metadata, and provides a password-protected **React** web UI for reviewing and correcting results.

Three detectors cross-validate every track — **deeprhythm** (CNN), **essentia** RhythmExtractor2013, and **librosa** — so octave errors are caught automatically and disagreements are flagged for manual review.

**Optional Spotify grabber** (`GRABBER_ENABLED=true`): watch your own Spotify playlists (add by URL or browse your account's playlists in-app), download the tracks you're missing (Deezer via your own ARL, yt-dlp fallback), transcode to one format, tag + BPM-analyze, and file them into your library by a path template — with an ambiguity inbox and ntfy pings.

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
| `PRESERVE_MTIME` | `true` | Keep each file's modified time after tagging |
| `WORKERS` | `1` | Parallel analysis threads (+~500 MB RAM each for deeprhythm) |
| `REFRESH_HASHES` | `false` | Recompute hashes before scanning (migration from pre-1.0.0) |
| `BPM_MIN` | `60` | BPM floor — values below are doubled |
| `BPM_MAX` | `200` | BPM ceiling — values above are halved |
| `USE_DEEPRHYTHM` | `false` | CNN detector (~500 MB/worker); **only effective on the `:full` image** — ignored on `:latest` (slim, no PyTorch) |
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

> **⚠️ LAN access only by default.** The UI runs over plain HTTP. Place a reverse proxy (nginx, Caddy) with TLS in front of port 5000 before exposing it outside your local network.

- **Sidebar navigation** grouped into Library / Tagging / Grabber / System sections with icons, collapsible to an icon-only rail; scan controls (Start / Pause / Resume / Stop) and a live status dot; becomes a hamburger menu below 1100 px with the same sections
- Library table with BPM, confidence, detector info, filter pills (All / Review / Locked / **Deleted**), and `pending` badge before analysis; **live search** as you type; **BPM ± tolerance filter**; error badge tooltips; back navigation restores filter/page/search state
- **Artists & Albums browse views** — a Tracks | Artists | Albums switcher with filterable card grids (track counts, years, average BPM) linking into the per-artist/per-album pages
- **Cover art everywhere** — embedded covers on library rows, browse cards, artist/album pages and track detail, with a show/hide toggle and cached delivery
- **Artist images** — local `artist.jpg` (Navidrome convention), or opt-in fetching from Deezer's public API (`FETCH_ARTIST_IMAGES`), cached on disk; falls back to album art
- BPM Review queue — Prev/Next navigation, Approve without re-analysing; approved/locked tracks marked `reviewed` and removed from queue
- Audio player with real waveform scrubbing and tap-tempo (Space bar)
- Persistent player bar with **Play all / Shuffle** queueing, prev/next, repeat, volume, a **queue viewer** (jump/remove/reorder), a reload-persistent queue that resumes at the saved position, keyboard shortcuts, and a ducking **preview** from detail/compare views
- **Artist & album pages** and a cadence ½×/2× BPM filter for running
- Duplicate resolution — a dedicated **Duplicates** page; step through groups side-by-side (stacked on mobile) and move unwanted copies to a recoverable **trash** (purged from Settings)
- ISRC lookup (Deezer / Spotify / MusicBrainz) on track detail & compare, plus a **bulk "Fill missing ISRCs"** with a duration-match guard
- **Re-analyze** button on track detail — re-runs detection for a single track without a full scan
- Save & Lock corrected BPM; Unlock for re-analysis
- Stats — BPM histogram with peak highlight and median marker, detector breakdown, Reviewed card, Retry Errors; with the grabber on, a **Library sources** card (grabbed vs pre-existing, downloads per provider, duplicates / ISRC / playlist-coverage rollups)
- Settings — live config changes without restart; `/healthz` JSON endpoint

---

## Supported Formats

`.mp3` · `.flac` · `.ogg` · `.opus` · `.m4a` · `.aac` · `.wav` · `.wv`

---

## Changelog

Full history: [CHANGELOG.md](https://github.com/PauloJf/BPM-Tagger/blob/main/CHANGELOG.md)

**v2.4.0** — Sectioned, collapsible sidebar navigation with icons (renames: BPM Review, Add Music). Artists & Albums browse views with a Library switcher. Cover art across the UI (rows, cards, artist/album/track pages) with a show/hide toggle, plus artist images from a local `artist.jpg` or an opt-in Deezer fetch. Spotify "Browse my playlists" picker; player restores position across reloads.

**v2.3.1** — Inbox "Search all again" (bulk re-search); duplicate resolution now jumps to the next group.

**v2.3.0** — Artist & album pages (Play all/Shuffle). Player queue viewer (jump/remove/reorder), add-to-queue / play-next, queue persistence, volume, and keyboard shortcuts. Library search over indexed tags, a "No ISRC" filter, and a cadence ½×/2× BPM match for running. Duplicate "Keep" / "Not a duplicate" / suggested-keep; ISRC validation + cancellable bulk fill.

**v2.2.0** — ISRC tools: "Find ISRC" (Deezer / Spotify / MusicBrainz) on track-detail & compare views, and a bulk "Fill missing ISRCs" that auto-writes confident duration-matched results and lists the rest to choose. Queue "Retry all failed"; player title links to the track detail.

**v2.1.0** — Deezer download provider (streamrip, via your own ARL; Monochrome/Tidal on hold) with a new `mp3-128` default output. Player gains Play all / Shuffle / repeat queueing and a ducking preview from detail/compare views. Duplicate resolution: step through groups and move unwanted copies to a recoverable trash (purged from Settings, triggers a Navidrome rescan). Inbox "Search again". Nav collapses to a hamburger below 1100 px with a pinned header.

**v2.0.0** — Music Grabber + React UI: watch Spotify playlists and download missing tracks (Monochrome/Tidal → yt-dlp fallback), transcode to one format, tag + BPM-analyze, ambiguity inbox, download queue, manual search & grab, metadata editor. Full React SPA (replaces the Jinja UI) with a persistent waveform player bar and light/dark theme.

**v1.1.0** — Deleted file detection: files removed from disk are automatically marked `deleted` in both watch mode (real-time) and scan mode (post-discovery diff). Locked tracks are never marked deleted. Deleted tracks reappear in the queue if the file comes back. New Deleted filter pill and Statistics card in the web UI.

**v1.0.8** — Live search, BPM ± filter, back-navigation state, error tooltips, Reviewed stat card, histogram peak/median, Navidrome watch-mode rescan, DeepRhythm memory logging, and several bug fixes.

**v1.0.6** — Bug fix: locking a track no longer triggers an infinite re-scan loop; Re-analyze button on track detail page.

**v1.0.5** — Build fix: Docker image now always built from `main`, preventing stale images from misplaced tags.

**v1.0.4** — Reviewed status badge; playback buffer setting (prevents NAS stuttering); auto-fix for full-library rescan after upgrade; SVG favicon.

**v1.0.3** — Slim image (~400 MB, no PyTorch) as default `:latest`; new `:full` tag adds deeprhythm CNN (~1.8 GB).

**v1.0.2** — Mobile nav (hamburger menu, scroll-strip settings sidebar, scan controls on small screens).

**v1.0.1** — Two-phase scan: all files registered as `pending` before analysis; interrupted scans resume.

**v1.0.0** — First stable release: full UI redesign, real waveform, tap-tempo, CSS histogram.
