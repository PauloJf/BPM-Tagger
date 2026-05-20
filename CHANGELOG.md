# Changelog

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
