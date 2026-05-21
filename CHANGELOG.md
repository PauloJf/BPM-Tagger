# Changelog

## v1.0.6 — 2026-05-21

**Bug fix**
- File watcher no longer re-analyzes locked tracks — locking a track via the UI no longer triggers an infinite re-scan loop caused by repeated tag writes

**Track detail**
- Re-analyze button: re-runs BPM detection for a single track immediately, without starting a full library scan

## v1.0.5 — 2026-05-21

**Build fix**
- Docker publish workflow now always checks out `main` regardless of which commit a tag points to — prevents stale builds when a tag is pushed from an older branch or wrong commit

## v1.0.4 — 2026-05-20

**Reviewed status**
- Approving or locking a flagged track now sets status to `reviewed` (green badge) instead of silently clearing the flag
- Reviewed tracks are excluded from the review queue and from `scan_review` / report re-analysis
- Re-analyzing an unlocked track resets `reviewed` back to `ok` or `review` based on the new result

**Playback buffering**
- `preload="auto"` — browser begins buffering audio as soon as the track page loads
- New **Playback Buffer** setting (default 3 s): play button waits until that many seconds are buffered before starting; shows a spinner while waiting — prevents stuttering on NAS / slow storage
- Configurable in Settings → Playback (0–30 s), persisted to `settings.json`

**Rescan-after-upgrade fix**
- Scanner auto-detects stale pre-tag hashes on startup: if >50% of done tracks show mismatches on a non-forced scan, hashes are refreshed in-place before queuing — prevents a full library rescan after upgrading from an older version
- New **Refresh Hashes** button in Settings → Scan for manual triggering
- Warning banner in Settings → Mode when `watch_all` or `scan_all` is stored (both re-analyze everything on every restart)

**Other**
- SVG favicon (purple gradient square with EQ bars)
- Version check no longer shows a 404 error when no GitHub releases have been published yet

## v1.0.3 — 2026-05-20

**Docker image size**
- New slim (default) image ships without PyTorch — ~400 MB instead of ~1.8 GB; suited for NAS and low-memory devices
- New `:full` tag includes PyTorch CPU + deeprhythm CNN for maximum accuracy on servers with spare RAM
- `USE_DEEPRHYTHM` now defaults to `false` in code (was `true`), consistent with `docker-compose.yml`
- `docs/` (screenshots) excluded from Docker build context via `.dockerignore`
- GitHub Actions workflow moved to `main`; publishes both `:latest` (slim) and `:full` on every version tag

## v1.0.2 — 2026-05-20

**Mobile UI**
- Nav bar collapses to a hamburger menu at ≤700 px — links, scan controls (with labels), and Logout all accessible from the dropdown panel
- Scan-status dot always visible in the mobile top bar so you can see Analyzing / Paused / Idle without opening the menu
- Settings sidebar becomes a horizontal-scroll pill strip on mobile instead of a stacked list
- Segmented mode control, sliders, and text inputs all adapt to narrow viewports

## v1.0.1 — 2026-05-20

**Two-phase scan**
- Discovery phase now runs first: every audio file found on disk is immediately registered in the library with status `pending`, so the full library is visible in the UI before analysis begins
- Processing phase then works through all pending tracks; interrupted scans resume naturally — pending entries survive restarts
- Library table shows a `pending` badge for tracks not yet analysed
- Statistics page "Pending" card uses the explicit count instead of deriving it

## v1.0.0 — 2026-05-18

First stable release.

**UI redesign (all screens)**
- New design system: oklch colour tokens, Inter Tight + JetBrains Mono (self-hosted), card layouts, animated scan banner, detector-bar visualisation
- Login page: shake animation on wrong password, decorative waveform bars, lockout state
- Library: CSS grid table, confidence bars, filter pills (All / Review / Locked) with live counts during scan
- Review queue: card-based layout with two-column grid and DetectorBar SVG showing all three detector values
- Track detail: real waveform computed server-side and stored in the database; click/drag scrubbing with touch support; redesigned tap-tempo button with ripple animation
- Stats: CSS flex histogram replacing canvas, six-card summary grid
- Settings: two-column layout with sidebar nav, toggle switches, number steppers, segmented controls
- About page with project story, authors, and tech stack

**Scanner improvements**
- Fixed hash capture after tag write — prevents re-analysing already-tagged files on every restart
- Stop button now cancels in-flight futures immediately instead of waiting for the full batch
- `REFRESH_HASHES=true` option to recompute stored hashes before scanning (migration path for libraries processed by older versions)
- Waveform peaks computed during BPM analysis (while audio is in OS page cache) and stored in SQLite — track detail page loads waveform instantly on subsequent visits; concurrent waveform requests for the same file are deduplicated

**Other**
- Restart button in Settings — replaces the process in-place via `os.execv`; browser reconnects automatically
- `bpm_confidence` column added to the database
- Duplicate Jinja filter registration cleaned up
