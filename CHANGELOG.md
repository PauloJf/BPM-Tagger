# Changelog

## v2.5.1 — 2026-07-14

- **Queue similar from the player:** a new **similar-tracks** button on the player bar and a **≈ Similar** button on the Run page's queue view list tracks in the style of the now-playing artist (Deezer artist radio, keyless — same source as the Related panel). Tracks **you already have** get a **Queue** button that appends them straight to the **play queue** — "keep this vibe going" without leaving the player; tracks you're missing get a **Grab** button feeding the download queue (grabber only). During a tempo-locked run, in-library matches are **cadence-checked first**: a track whose octave-folded BPM can't stretch onto the active target within the stretch cap shows **off cadence** instead of a Queue button, so a run's queue never picks up a track your feet can't follow. A **Queue all** shortcut queues every eligible in-library match at once; rows already queued show **✓ in queue**, and the 30-second ▶ previews work here too.

## v2.5.0 — 2026-07-14

- **Suggestions page (grabber):** a new **Suggestions** page (`/suggestions`) recommends **artists you don't have yet** and **tracks worth grabbing**, derived from what's already in your library — your top and **starred** artists weigh heaviest as seeds. Each suggested artist expands to its top tracks, and any track is one click to **Add to queue** (feeding the existing grab pipeline). Artists you already own (3+ tracks) are filtered out; artists you've _sampled_ (1–2 tracks) still surface, badged with what you have; suggested tracks your library already resolves are dropped. Anything you dismiss stays gone across refreshes and restarts. Refresh on demand — it also auto-refreshes when stale (weekly). Powered by the **Deezer public catalog API** (keyless — no account or key, and it works even when Spotify isn't connected); Spotify, when connected, only enriches enqueues with a confident track match for better dedupe. All Deezer traffic shares the existing rate limiter.
- **Related panel:** every **Artist**, **Album** and **Track** page now has a collapsible **Related · powered by Deezer** panel — similar artists and similar tracks, looked up live only when you expand it (nothing fetched from merely opening a page). Similar artists you already have link straight to their library page and show how many tracks you own; clicking any artist opens the **artist detail popup** (below). It's read-only and works even with the grabber off (in-library links stay useful); with the grabber on, similar tracks you don't have get an **Add to queue** button. Results are cached server-side per artist for the process lifetime, so hopping between pages doesn't re-hit Deezer.
- **Artist detail popup:** clicking a suggested artist (or a related artist in any Related panel) opens a popup with a short **description** (best-effort, via the keyless MusicBrainz → Wikidata → Wikipedia chain), the artist's **top tracks**, and their **discography** split into **albums** and **singles/EPs**. Expand any release to see its tracklist, add individual tracks, or **Add all** to queue the whole album/single at once (tracks already in your library or queue are skipped). All catalog data is from Deezer's keyless public API.
- **30-second track previews:** suggested and related tracks show a ▶ **preview** button that plays Deezer's 30-second clip through the normal player — start one while the queue is playing and it ducks the music down, plays the clip, then fades back in exactly where it left off (a slim seekable progress bar replaces the waveform for preview clips). Preview clips never persist into your saved queue. (Deezer's preview hosts are allowed in the media CSP.)
- **Now licensed under AGPLv3:** BPM Tagger moves from MIT to the **GNU Affero General Public License v3.0 or later** (AGPL-3.0-or-later) — see the new `LICENSE` file, the README, and the About page. The About page also gets a small refresh (two-column intro noting the optional library/Run-mode/suggestions tools).

## v2.4.4 — 2026-07-14

- **Dislike toggle:** star's opposite number — dislike a track from the run queue, the track page, or the library so it's never picked for a run again. A **Disliked** filter pill in the library lists (and lets you undo) disliked tracks; the run-queue builder drops them before scoring, so they're excluded from both the initial queue and every auto-refill.

## v2.4.3 — 2026-07-14

- **Run mode, phone-first:** everything down to the transport now fits a single phone screen — the cover art scales with the viewport, the presets/steps are more compact, and the BPM Locked/Unlocked pill became a **lock icon beside the target BPM**. The global player bar hides on `/run` (the run player is the transport; stream errors surface on the page instead). A new **Queue** option on the Presets/± Steps switcher shows the run queue in place — jump to a track, star the keepers, per-row `native · octave · stretch → result` math — and also lists a queue restored after a reload. A **lyrics** button joins the transport, and the track title links to the track page; **saving a corrected BPM there re-stretches a live tempo lock instantly**, no rebuild needed.
- **Run queue auto-refill:** when the last queued track starts playing, a fresh batch for the same target is fetched and appended automatically, so the run never goes silent (skipped when repeat is on). The refill tells the server which tracks are already queued (a bounded recent-history window) so it fills in unplayed matches first instead of just reshuffling the same batch; if every eligible match is already queued — a small library on a long run — the server recycles the full pool rather than starving the refill.
- **Playback resilience:** a failed stream no longer leaves the player dead — pressing play reloads the source and retries; an expired session is told apart from a genuinely missing file, routes to the sign-in screen, and playback resumes with one tap after logging back in. On iOS, queue advance reuses the audio element synchronously so lock-screen playback survives track changes, and an auto-advance vetoed in the background resumes when the app is next visible.
- **Tag writing:** BPM tags now write correctly to ID3-backed containers (WAV, AIFF, DSF) using proper ID3 frames, and files without any tag header get one created instead of failing.

## v2.4.2 — 2026-07-13

- **Run mode:** a dedicated full-screen tempo-run player page (`/run`), like the cadence apps but built on your own library and BPM tags — cover art and track details up top, a big **target BPM** readout with a live `native · stretch × octave → result` breakdown, a **BPM Locked/Unlocked** pill, **± Steps** (−5/−1/+1/+5) or four **named presets** (name + value definable in Settings → Run Mode, defaults Warmup 120 / Easy 155 / Steady 165 / Tempo 175), a scrubbing **waveform** with elapsed/remaining time, and large play/prev/next buttons with the queue below. **Start run** queues the tracks whose **octave-folded** BPM (×½/×1/×2 — a 75 BPM song serves a 150 cadence at native speed) sits within a configurable tolerance, **preferring starred tracks**; each queue row shows the same native → result math. The **BPM lock** stretches every track onto the exact target with the browser's pitch-preserving time-stretcher, capped by a max-stretch setting; nudging the target mid-run re-stretches the current track instantly. The player-bar BPM readout switches to the locked cadence with a lock icon and links back to the Run page. Queue size, octave matching, tolerance, starred preference and stretch cap are all configurable. The installed PWA **opens straight on the Run page** (`start_url`); the browser version still lands on the library.
- **Starred tracks:** star favourites from the library rows, the track page, or the run queue itself; a **Starred** filter pill in the library, and run queues fill with starred tracks first.
- **Find metadata:** the track metadata editor can now fill every field from **Spotify** (when connected) and **Deezer** instead of hand-typing — resolved directly **by ISRC** when the field is set (both services support exact-recording lookup), otherwise by artist + title, falling back to the filename. Candidates show cover, album, year, track #, ISRC, and a duration Δ against the file; clicking a candidate opens a **detail view** (large cover, every field, differences vs the current form highlighted with the current value alongside). **Use** fills the form for review before saving. Deezer calls go through the shared rate limiter.
- **Installable web app (PWA):** the UI now ships a web-app manifest, home-screen icons and a minimal service worker, so it can be installed on a phone (Android: Chrome menu → *Install app*; iOS: Share → *Add to Home Screen*) and runs standalone. The service worker does **no caching** — the app and audio always stream from your server, so nothing can go stale. Install requires HTTPS (reverse proxy, or `tailscale serve` for a private tailnet URL). The player also wires the **Media Session API**: lock-screen / headset play-pause-next-prev controls with title, artist and cover art.

## v2.4.1 — 2026-07-13

- **Lyrics:** fetch plain or **synced (LRC)** lyrics from [LRCLIB](https://lrclib.net) (free, community-run, no account needed). A **Lyrics** card on the track page shows/edits them (paste LRC lines for synced), a **bulk fill** (Settings → Lyrics) covers the whole library — pre-existing embedded lyrics and `.lrc` sidecars are indexed rather than re-fetched, and not-founds are remembered so re-runs stay cheap (a checkbox retries them). Storage is configurable: **embed** in the file tag (`USLT` / `LYRICS=` / `©lyr`) or a **`.lrc` sidecar** — Navidrome reads both. `LYRICS_ENABLED` additionally auto-fetches lyrics for every track the grabber downloads. Lookups match on artist + title + album + **duration**, so a live/remix version's lyrics aren't grabbed for the studio cut.
- **Player BPM pulse:** the player bar shows the current track's BPM with the beat-pulsing dot from the track page — it flashes on every beat while playing (still when paused), a quick sanity check that the detected tempo matches the music.
- **Player lyrics drawer:** a mic button on the player bar opens the current track's lyrics. Synced (LRC) lyrics **follow playback** — active line highlighted and centered, click any line to seek there, hand-scrolling pauses the follow briefly; plain lyrics step manually (click a line or ▲/▼). Lyric-less tracks offer a one-click LRCLIB fetch in place.
- **Image editing:** an **image picker** (searches **Spotify** when connected + **Deezer**, or paste a URL / upload a file) is now available in three places — **track cover** (track detail), **album cover** (album page, embeds into *every* track of the album in one go), and **artist image** (artist page; sets a custom image that outranks `artist.jpg` and the auto-fetch, with a *Remove custom image* fallback). All embeds refresh the stored file hash so the watcher never re-analyzes edited files.
- **Deezer rate limiting:** every Deezer public-API call (artist-image auto-fetch, image-picker search) now goes through one app-wide sliding-window limiter (25 requests / 5 s — half of Deezer's quota), so a first-time load of a large Artists grid queues briefly instead of tripping quota errors that used to be mis-recorded as daily misses.
- **Artist images in the library:** new opt-in `ARTIST_IMAGES_TO_LIBRARY` (Settings → Artwork) saves fetched *and* hand-picked artist images as `artist.jpg` in the artist's own folder — Navidrome sees them too, and the app never re-fetches that artist. Only folders that exclusively contain the artist's tracks are written to; flat/shared layouts keep using the app cache.
- New env vars / settings: `LYRICS_ENABLED` (default `false`), `LYRICS_MODE` (`embed` | `sidecar`), `ARTIST_IMAGES_TO_LIBRARY` (default `false`); new **Settings → Lyrics** section.

## v2.4.0 — 2026-07-13

- **Navigation:** the top bar is now a **sidebar** with grouped sections — Library, Tagging, Grabber (when enabled) and System — so the tagging workflow, the grabber workflow and app chrome are no longer interleaved. Every entry has an icon and the sidebar **collapses to an icon-only rail** (state remembered), with the player bar starting past it either way. Two renames for clarity: **Review → BPM Review** and **Search → Add Music**. Small screens keep the top bar + hamburger menu, now with the same section headers.
- **Artwork:** embedded cover art now shows across the library — thumbnails on library rows and the Artists/Albums browse cards, an artist image and per-album covers on the artist page, a cover header on the album and track pages. A **show/hide artwork** toggle (remembered per browser) keeps things light on slow libraries; covers are served with cache headers so grids don't re-extract art on every visit.
- **Artist images:** resolved from an `artist.jpg`/`artist.png` beside the artist's files (Navidrome's convention), else — **opt-in** via `FETCH_ARTIST_IMAGES` or **Settings → Artwork** — fetched once from Deezer's public API (no account needed) and cached under `/data/artist_images/`. Unresolved artists fall back to their album art.
- **Library:** new **Artists** and **Albums** browse views (a Tracks | Artists | Albums switcher on the Library page) with per-entry track counts, years and average BPM, linking into the existing artist/album pages. Compilation guests are grouped under the album artist.
- **Playlists:** a **Browse my playlists** picker lists your Spotify account's playlists (owned + followed) so you can add one to watch without copy-pasting a URL; already-watched playlists are flagged.
- **Player:** reloading the page now restores the current track **at its saved position** and resumes playback if it was playing (browsers may block the auto-resume until you interact — it then stays paused at the right spot).
## v2.3.1 — 2026-07-07

- **Inbox:** a **Search all again** button re-runs the default search for every waiting item at once (e.g. after enabling a new provider).
- **Duplicates:** resolving a group (**Keep**, trash, or **Not a duplicate**) now jumps to the next group — trashing a single copy advances only once the group drops below two tracks — so you can work through them without returning to Stats.

## v2.3.0 — 2026-07-07

**Browse by artist & album**
- New **artist** and **album** pages — album-grouped track lists with Play all / Shuffle, reached from the player bar, track detail, and each other.

**Player & queue**
- **Queue viewer** — a drawer off the player bar shows the upcoming queue with jump-to, remove, and reorder.
- **Add to queue** / **Play next** buttons on library, artist and album rows.
- The queue **persists across reloads** (restored paused), a **volume** control, and **keyboard shortcuts** (`k` play/pause, `←/→` prev/next, `+/-` volume, `m` mute).

**Library & search**
- Search now matches indexed **title/artist/album**, not just the file path.
- A **No ISRC** filter pill, and a **cadence ½×/2×** BPM toggle so a running cadence also matches half/double-time tracks.

**Duplicates**
- **Keep** (trash the other copies in one click), **Not a duplicate** (dismiss a group), and a **suggested-keep** hint (best by format/BPM).

**ISRC**
- Format validation on writes, and the bulk fill is now **cancellable**.

## v2.2.0 — 2026-07-07

**ISRC tools**
- **Find ISRC** on the track-detail and duplicate-compare views — look up a track's ISRC from Deezer, Spotify and MusicBrainz and pick a candidate (an Open-in-Spotify search link when nothing matches).
- **Bulk fill** (Settings → ISRC) — look up every library track missing an ISRC and write it. A confident, **duration-matched** single result is filled automatically; anything uncertain or not found is listed with its candidates for you to choose. The duration guard avoids writing a remix/live version's ISRC.
- Editable ISRC field per column in the compare view, saved without disturbing other tags.

**Queue & player**
- **Retry all failed** re-queues every failed grab at once (e.g. after enabling a provider).
- The player bar's track title now links to that track's detail page.

## v2.1.0 — 2026-07-07

**Deezer download provider; Monochrome on hold**
- New **Deezer** provider (via [streamrip](https://github.com/nathom/streamrip)) using your own Deezer ARL. A free-tier ARL returns full-length tracks at MP3 128 kbps (MP3 320 / FLAC require a paid subscription). Deezer search also supplies ISRCs, improving library matching. Configure with `DEEZER_ARL` / `DEEZER_QUALITY`; a "Test" button in Settings validates the ARL.
- Default `PROVIDER_ORDER` is now `deezer,ytdlp`. The **Monochrome/Tidal** provider is on hold pending investigation and is skipped regardless of configuration (`MONOCHROME_ON_HOLD`).
- Added an `mp3-128` transcode profile; default `OUTPUT_FORMAT` is now `mp3-128` (matches the free Deezer source, avoiding a wasteful upscale — Deezer `.mp3` passes through without re-encoding).

**Player: queue, shuffle & ducking preview**
- **Play all** / **Shuffle** the current filtered library view; the player bar gains prev/next, shuffle and repeat (off/all/one) controls with a queue position indicator.
- Playing a track from a track detail, review or compare view now **previews** it — the queue fades and ducks, then resumes where it left off when the preview ends or you leave the page.

**Duplicates**
- Walk duplicate groups with **Prev/Next** directly in the compare view.
- **Resolve duplicates**: move the unwanted copy to a recoverable **trash** (a soft delete outside the library), which triggers a Navidrome rescan so it drops from the library. A **Trash** panel in Settings shows the current count + size and can **purge** it. Locked tracks are protected from deletion.

**Inbox**
- **Search again** re-runs a queued match's default search with the item's own metadata (e.g. after enabling a new provider), and the edit-search box is pre-filled with the original query.

**UI**
- The top navigation collapses to a hamburger below 1100 px (fixes the off-centre layout between 700–1024 px) and the header stays pinned while scrolling.

## v2.0.1 — 2026-07-07

**Preserve file modified time when tagging**
- Writing a BPM tag no longer bumps the file's modified time. The original timestamp is restored after the tag write, so Navidrome rescans, backup tools and sort-by-date views are left undisturbed.
- New `PRESERVE_MTIME` setting (default `true`), exposed as a "Preserve file date" toggle in the web UI. Setting it in docker-compose locks the toggle — the environment stays authoritative and the UI cannot override it.

## v2.0.0 — 2026-07-07

Major expansion into a Spotify→library sync + downloader, on a full UI rewrite.

**Architecture**
- Refactored the `bpm_tagger.py` / `web_ui.py` monoliths into the `bpm_tagger` package (config, db, bpm, scan, notify, integrations, grabber, web) with a characterization test suite.
- Web UI migrated from server-rendered Jinja to a **React SPA** (Vite + TypeScript + Tailwind); Flask now serves a JSON API + the built bundle. Tightened CSP; added a light/dark theme toggle.

**Music grabber (opt-in, `GRABBER_ENABLED=true`)**
- Spotify playlist sync (Authorization Code OAuth) → have/missing/queued reconciliation against the library by ISRC or fuzzy score.
- Download pipeline: Monochrome (Tidal) → yt-dlp fallback → ffmpeg transcode to one format → full tags + cover → path-template filing → 3-detector BPM analysis; grabbed files marked `managed` (watcher-safe).
- Ambiguity **inbox** (choose / re-search / skip) with ntfy pings; download **queue** with progress/retry/cancel/history.
- Metadata + cover editor with template rename; m3u export; duplicate report; dry-run.

## v1.1.0 — 2026-05-22

**Deleted file detection**
- Watch mode: when a file is deleted or moved while the container is running, the track is marked `deleted` in the database immediately via filesystem events
- Scan mode: at the start of every scan, the discovered file list is compared against all tracked paths; any file no longer on disk (and not locked) is marked `deleted` automatically
- Locked tracks are never marked deleted — if a volume is temporarily unmounted, locked tracks are preserved
- Deleted tracks automatically re-enter the analysis queue if the file reappears on disk

**Web UI**
- New **Deleted** filter pill on the Library page with a live count
- Deleted tracks are hidden from the default **All** view and the **Review** queue — visible only when the **Deleted** filter is active
- New **Deleted** summary card on the Statistics page

## v1.0.8 — 2026-05-21

**Library**
- Search field filters tracks as you type (300 ms debounce) — no Enter required
- New BPM ± tolerance filter: enter a target BPM and allowance to narrow the list to a specific range
- Going back from track detail returns to the exact library state — same filter, page, and search query
- Hovering an `error` badge shows the full error message as a tooltip

**Statistics**
- New **Reviewed** summary card
- Histogram: peak bucket highlighted in a distinct colour; vertical median line

**Watch mode**
- Navidrome rescan now triggers once when the file queue drains after tagging new files (60 s cooldown) — previously never fired in watch mode
- DeepRhythm model load and release logged at INFO level

**Bug fixes**
- Re-analyze spinner rotation axis corrected
- Review count badge and library "Review" filter no longer count locked or already-reviewed tracks
- Startup migration clears stale `needs_review` flags on locked tracks from pre-v1.0.4 databases
- Locking a track no longer triggers an infinite re-scan loop
- Re-analyze button on track detail page for on-demand single-track re-analysis

## v1.0.7 — 2026-05-21

**Bug fixes**
- Re-analyze spinner now rotates around its centre instead of the corner
- Review count badge and library "Review" filter no longer count locked or already-reviewed tracks
- Startup migration clears stale `needs_review` flags on locked tracks from pre-v1.0.4 databases

**Library**
- Search field filters tracks as you type (300 ms debounce)
- New BPM ± tolerance filter
- Going back from track detail returns to the exact library state
- Error badge tooltip shows the full error message

**Statistics**
- New Reviewed summary card
- Histogram peak and median marker

**Watch mode**
- Navidrome rescan triggered once when queue drains
- DeepRhythm model load/release logged at INFO level

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
