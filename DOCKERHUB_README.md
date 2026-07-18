# BPM Tagger

Auto-detects BPM for every track in your [Navidrome](https://www.navidrome.org/) library, writes it back to the file's metadata, and provides a password-protected **React** web UI for reviewing and correcting results.

Three detectors cross-validate every track — **deeprhythm** (CNN), **essentia** RhythmExtractor2013, and **librosa** — so octave errors are caught automatically and disagreements are flagged for manual review.

**Multi-source playlists:** watch playlists from **Spotify** and **Navidrome**, reconciled against your library (have / missing / new / removed) and usable as **Run-mode sources**. Navidrome playlists work with just your Navidrome credentials — no grabber. With the **optional Spotify grabber** (`GRABBER_ENABLED=true`) you can also add Spotify playlists by URL and **download the tracks you're missing** (Deezer via your own ARL, yt-dlp fallback), transcode to one format, tag + BPM-analyze, and file them into your library by a path template — with an ambiguity inbox and ntfy pings. A **Suggestions** page recommends artists and tracks to grab next, derived from your library via the keyless Deezer catalog.

Source & full docs: [github.com/PauloJf/BPM-Tagger](https://github.com/PauloJf/BPM-Tagger) · Licensed under [AGPL-3.0-or-later](https://www.gnu.org/licenses/agpl-3.0.html)

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
| `UI_SESSION_HOURS` | `24` | Admin session length — signed out after this many idle hours (sliding) |
| `RUN_PASSWORD` | _(empty)_ | Optional second password for a locked-down **player mode** (Run page + a runner-focused About only). Also settable in Settings → Player Access |
| `RUN_SESSION_DAYS` | `30` | How long a player login stays signed in (days) |
| `INSTALL_PING` / `INSTALL_PING_URL` | _(ask on first run)_ | Opt-in anonymous install ping (version only; no identifier/data/cookies). Set `INSTALL_PING=false` or `INSTALL_PING_URL=""` to disable |
| `NTFY_TOPIC` | _(empty)_ | ntfy topic (leave empty to disable) |
| `NAVIDROME_URL` | _(empty)_ | Trigger Navidrome rescan after each scan |
| `NAVIDROME_STAR_SYNC` | `false` | Two-way star sync toggle (Settings → Navidrome) |
| `NAVIDROME_SCROBBLE` | `false` | Scrobble built-in-player plays to Navidrome (Settings → Navidrome) |
| `LYRICS_ENABLED` | `false` | Auto-fetch lyrics (LRCLIB) for tracks the grabber downloads; manual/bulk fetch always available in the UI |
| `LYRICS_MODE` | `embed` | Store lyrics in the file tag (`embed`) or as a `.lrc` sidecar (`sidecar`) |

All variables documented in [`docker-compose.yml`](https://github.com/PauloJf/BPM-Tagger/blob/main/docker-compose.yml).

---

## Web UI

Set `ENABLE_UI: "true"` and a strong `UI_PASSWORD`, then open `http://your-host:5000`.

> **⚠️ LAN access only by default.** The UI runs over plain HTTP. Place a reverse proxy (nginx, Caddy) with TLS in front of port 5000 before exposing it outside your local network — and set `UI_TRUSTED_PROXIES` to the number of proxies so the login lockout keys on the real client IP.

The UI password is stored as a salted hash once changed in **Settings** (never plaintext), a password change logs out all other devices, and `settings.json` is written `0600`.

- **Sidebar navigation** grouped into Library / Tagging / Grabber / System sections with icons, collapsible to an icon-only rail; scan controls (Start / Pause / Resume / Stop) and a live status dot; becomes a hamburger menu below 1100 px with the same sections
- Library table with BPM, confidence, detector info, filter pills (All / Review / Locked / **Deleted**), and `pending` badge before analysis; **live search** as you type; **BPM ± tolerance filter**; error badge tooltips; back navigation restores filter/page/search state
- **Artists & Albums browse views** — a Tracks | Artists | Albums switcher with filterable card grids (track counts, years, average BPM) linking into the per-artist/per-album pages
- **Cover art everywhere** — embedded covers on library rows, browse cards, artist/album pages and track detail, with a show/hide toggle and cached delivery
- **Artist images** — a custom image you pick, local `artist.jpg` (Navidrome convention), or opt-in, rate-limited fetching from Deezer's public API (`FETCH_ARTIST_IMAGES`), cached on disk; falls back to album art. `ARTIST_IMAGES_TO_LIBRARY` saves fetched/picked images as `artist.jpg` in the artist's folder so Navidrome sees them too
- **Image editing** — change a track's cover, set an album cover across all its tracks at once, or pick a custom artist image — searching Spotify/Deezer, pasting a URL, or uploading a file
- **Lyrics** — plain or synced (LRC) lyrics from LRCLIB per track or in bulk, viewable/editable on the track page, stored embedded or as `.lrc` sidecars (Navidrome-compatible); a **player lyrics drawer** follows synced lyrics live (click a line to seek) and steps plain lyrics manually
- **Navidrome star sync** — two-way: stars set here push to Navidrome as favourites, stars set in Navidrome pull in (feeding Run mode's starred preference); per-track baseline merge, path + fuzzy matching, manual **Sync stars now** trigger
- **Navidrome scrobbling & play counts** — opt-in: built-in-player plays (Run mode included) scrobble to Navidrome at the halfway mark (reaching Last.fm/ListenBrainz through it); **Pull play counts** imports Navidrome's play counts, shown per track and usable as a **prefer familiar tracks** run-queue preference. Your BPM tags also power Navidrome **smart playlists** (`.nsp` with a `bpm` range) for cadence playlists in any Subsonic client
- BPM Review queue — Prev/Next navigation, Approve without re-analysing; approved/locked tracks marked `reviewed` and removed from queue
- Audio player with real waveform scrubbing and tap-tempo (Space bar)
- Persistent player bar with **Play all / Shuffle** queueing, prev/next, repeat, volume, a **queue viewer** (jump/remove/reorder), a reload-persistent queue that resumes at the saved position, keyboard shortcuts, and a ducking **preview** from detail/compare views
- **Floating mini player** — pop the now-playing card out into an always-on-top window (Document Picture-in-Picture, Chromium desktop) that survives tab/route changes: the **cover art fills the blurred background** behind a glass card, title/artist, seekable progress, prev/play/next, volume, and the Run-mode tempo-lock pill. **Click the Run page cover** to pop it out (button also in the player bar); as an installed PWA it tucks the main window away where the runtime allows; playback stays in the main tab and the window follows the app theme
- **Artist & album pages** and a cadence ½×/2× BPM filter for running
- **Related & artist explorer** — a collapsible **Related · powered by Deezer** panel on every artist/album/track page (similar artists + tracks, fetched on expand); click any artist for a popup with a short bio, top tracks and full discography (albums + singles/EPs), adding single tracks or a whole album to the download queue. **30-second previews** (▶) on suggested/related rows play through the ducking player. Read-only with the grabber off
- **Queue similar from the player** — a similar-tracks button on the player bar (and **≈ Similar** on the Run page's queue view): in-library matches queue straight onto the **play queue** (cadence-checked during a tempo-locked run — unstretchable tracks show **off cadence**), missing ones get a grabber-gated **Grab**, plus a **Queue all** shortcut
- **Suggestions** _(grabber)_ — a page of artists & tracks to grab next, seeded from your library's top/starred artists via the keyless Deezer catalog; one-click add-to-queue, dismissable, auto-refreshing
- **Run mode** — a full-screen tempo-run player that fits one phone screen: viewport-scaled cover art, big target-BPM readout with the tempo-lock toggle and a `native · stretch × octave → result` breakdown, ±1/±5 steps, four **named presets** or an in-place **queue view**, lyrics drawer, waveform + large transport; a **source picker** builds the queue from your whole library or a specific **playlist** (with an "N of M available" count); auto-queues octave-folded BPM matches (**starred tracks** first, **disliked tracks** never), **refills the queue automatically when the last track starts** (a playlist run prefers its own tracks and **tops up from your library at the same cadence** when the playlist is too small, so it never loops one song), and **locks the tempo** so every song stretches onto your step, pitch preserved; the track's **play count** shows under the title/artist, and the desktop track-info column shows **file audio quality** (format + bit depth / sample rate, or bitrate)
- Duplicate resolution — a dedicated **Duplicates** page; step through groups side-by-side (stacked on mobile) and move unwanted copies to a recoverable **trash** (purged from Settings)
- ISRC lookup (Deezer / Spotify / MusicBrainz) on track detail & compare, plus a **bulk "Fill missing ISRCs"** with a duration-match guard
- **Find metadata** — fill a track's whole tag set from Spotify/Deezer (directly by ISRC when known, else by artist + title or filename), review, then save
- **Re-analyze** button on track detail — re-runs detection for a single track without a full scan
- Save & Lock corrected BPM; Unlock for re-analysis
- Stats — BPM histogram with peak highlight and median marker, detector breakdown, Reviewed card, Retry Errors; a **Run mode** card (tracks played, time on feet, tempo-shifted vs native, average cadence, time per cadence) once you've done a run; with the grabber on, a **Library sources** card (grabbed vs pre-existing, downloads per provider, duplicates / ISRC / playlist-coverage rollups)
- Settings — live config changes without restart; `/healthz` JSON endpoint
- **Player mode** _(optional)_ — a second password (`RUN_PASSWORD` / Settings → Player Access) logs into a locked-down view showing **only the Run page** (plus a runner-focused About): play, star/dislike, and scrobble, but no access to the library, settings, or downloads (enforced server-side, default-deny). Ideal for a shared phone/tablet or a dedicated running device; its login stays signed in far longer (`RUN_SESSION_DAYS`, default 30). On desktop it keeps a slim sidebar with just **Run** + **About**; on phones that collapses to a compact top bar with the same tabs, and Run uses a one-screen layout with the waveform/transport pinned to the bottom
- **Installable as an app (PWA)** — add it to your phone's home screen (requires HTTPS in front of the UI); lock-screen/headset media controls via the Media Session API; no offline caching, everything streams live from your server

---

## Supported Formats

`.mp3` · `.flac` · `.ogg` · `.opus` · `.m4a` · `.aac` · `.wav` · `.wv`

---

## Support this project

If BPM Tagger has been useful to you, consider supporting its development:

- ☕ [Ko-fi](https://ko-fi.com/paulojf)
- 💜 [GitHub Sponsors](https://github.com/sponsors/PauloJf)

Every bit helps keep this project maintained and open source.

---

## Changelog

Full history: [CHANGELOG.md](https://github.com/PauloJf/BPM-Tagger/blob/main/CHANGELOG.md)

**v2.6.10** — **Run & mobile fixes + hardening.** The tempo-lock toggle now matches the **restored** lock after a reload; **star toggles work on auto-refilled** queue tracks; changing the **run source** prompts a Rebuild; and library top-ups in a playlist run are **dimmed** (not just italic). Mobile: the run-source picker moved **off the cover** into the Queue view, the Run queue no longer overflows the screen, and the **nav menu scrolls** so Logout/Support are reachable instead of hidden behind the player bar. Under the hood: server-side image fetches don't follow redirects (**SSRF** hardening), ffmpeg transcodes **time out**, the waveform cache is lock-guarded, and the database **enforces foreign keys** with orphan cleanup. Plus a forced-`Secure`-cookie option (`UI_FORCE_SECURE_COOKIE`) for TLS-terminating proxies, admin-only `/healthz` stats, a React **error boundary**, and **dependency scanning** in CI. And a one-time **"What's new"** popup after each update (admin only) with an in-app changelog view. More mobile polish: the app respects the device **safe area** so the player bar clears the home indicator on notched phones/PWAs, the **album/artist header** no longer squishes when its image is hidden, and the **Library toolbar** collapses BPM/cadence/per-page controls behind a **Filters** button on phones. Plus a **Delete** button on failed queue items.

**v2.6.9** — Run polish: the cover art is reliably **square and centered** (fixed off-center / non-square rendering on desktop and mobile); track details **hold steady** instead of blanking/jumping on track change; and in a topped-up playlist run, **library-added tracks show italic** in the queue to set them apart from the playlist's own.

**v2.6.8** — **Playlist runs top up from the library** — a run built from a playlist now prefers its matching tracks and fills the rest from your whole library at the same cadence when the playlist is too small, fixing a small playlist looping one song (which looked like "next" being stuck / one song playing, especially in player mode on a phone). Playlist detail pages now show each matched track's **BPM and length** and link to its track/artist/album pages.

**v2.6.7** — Fix: the Run page **cover art went missing** in 2.6.6 — the cover box collapsed to zero width in the cockpit's cover slot, leaving only the mini-player overlay. Restored, keeping the no-blank-on-track-change behaviour.

**v2.6.6** — **Mini player, Run page polish & run stats.** The floating mini player now fills its background with the **blurred cover** behind a glass card, and on the Run page the **cover itself is the pop-out button**; opening it as an installed PWA tucks the main window away where supported. The playing track's **play count** shows under the title/artist (mobile + desktop), the **cover no longer blanks/jumps** on track change, and mobile **player mode** gets a **sticky** top bar shared across Run + About. A playlist run now **stays scoped to that playlist** as it auto-refills. New **Run mode** Stats card: tracks played, time on feet, tempo-shifted vs native, average cadence, and time per cadence.

**v2.6.5** — **Floating mini player (Picture-in-Picture).** Pop the now-playing card out into an always-on-top window that survives tab switches — cover, title/artist, a **seekable progress bar**, prev/play/next, **volume**, and (in Run mode) the tempo-lock pill with the pulsing beat dot. Built on the Document Picture-in-Picture API (Chromium desktop); the pop-out button is in the player bar and on the Run page's cover, hidden where unsupported. Playback stays in the main tab; the window mirrors the app theme.

**v2.6.4** — **Playlists from Spotify _and_ Navidrome, as Run sources.** The Playlists page now watches **Navidrome** playlists too (pick from your own over Subsonic) alongside Spotify — reconciled against your library by metadata, with have/missing coverage; Navidrome playlists need only your Navidrome credentials, no grabber. Sync is now a **diff**, so each playlist flags **new** tracks and keeps **removed** ones as tombstones (added/removed counts on the card). **Run mode** gains a **source picker**: build the queue from your whole library or a specific playlist — scoped to its matched, BPM-tagged tracks with an "N of M available" count — shared to the player role too.

**v2.6.3** — **Run desktop cockpit polish.** The desktop track-info column now shows the playing track's **play count** (when Navidrome play data has been pulled) and **file audio quality** (format + bit depth/sample rate for lossless, or bitrate for lossy), read header-only on demand. Fixed cockpit crowding and premature scrolling on short/mid-width windows — the two-column cockpit collapses via a container query on its own width (not a viewport breakpoint that couldn't see the sidebar), the run queue is pinned to fill its column, and the page drops the unused player-bar padding. On short viewports the sidebar keeps its logo/collapse/footer pinned while only the nav sections scroll.

**v2.6.2** — **Player mode + session lengths + opt-in install count.** A second password (`RUN_PASSWORD` / Settings → Player Access) opens a locked-down **player** view showing only the Run page — play, star/dislike, scrobble, nothing else (server-side default-deny). Player logins stay signed in far longer than admin ones (`RUN_SESSION_DAYS`, default 30); the admin session length (`UI_SESSION_HOURS`, default 24) is now actually enforced as a sliding idle timeout. In player mode a slim sidebar (just **Run** + a runner-focused **About**) replaces the full admin nav on desktop; phones collapse that to a compact top bar with the same tabs, and the Run page uses a one-screen layout with the transport pinned to the bottom. On first run the UI asks once whether to send an **anonymous install ping** (app version only — no identifier, data, or cookies; IPs not logged); if you opt in it fires once per version (on install and after each update), so upgrades are counted. Decline and nothing is sent; change your mind under About. The admin Run page's **tap-tempo** card also moves behind a **Cover / Tap** toggle on desktop, swapping into the cover's slot when selected so the cockpit never shifts.

**v2.6.1** — **UI consistency pass**: every top-level page now shares one header layout (title · subtitle · right-aligned primary action, with search/filters on their own toolbar row) built from common components. The sidebar's active item is clearer and its scan status is promoted to a block with a live `completed / total` count and a progress bar while analysing. On desktop the Run page aligns to the same width and position as every other page (it was narrower/inset before), fills the width in the single-column layout, and its tap-tempo card holds a constant height across states.

**v2.6.0** — **Navidrome scrobbling & play counts**: opt-in scrobbling reports built-in-player plays (Run mode included) to Navidrome at the halfway mark — play counts, "last played" and Navidrome's Last.fm/ListenBrainz forwarding all see your runs. **Pull play counts** (Settings → Navidrome) imports Navidrome's per-song play counts, shown on the track page and powering a new Run-mode **prefer familiar tracks** queue option (most-played matches first, within the starred preference). New env vars `NAVIDROME_SCROBBLE`, `RUN_PREFER_FAMILIAR`.

**v2.5.2** — **Two-way star sync with Navidrome**: stars flow both ways — starred tracks here become Navidrome favourites, and Navidrome stars pull in (feeding Run mode's starred queue preference). Per-track three-way merge against a last-synced baseline (an un-star on one side is never mistaken for a star on the other); path matching tolerates differing container roots, with fuzzy fallback; failed writes retry next pass. Enable in **Settings → Navidrome**, run with **Sync stars now**. New env var `NAVIDROME_STAR_SYNC`.

**v2.5.1** — **Queue similar from the player**: a similar-tracks button on the player bar and a **≈ Similar** button on the Run page's queue view list tracks in the style of the now-playing artist (Deezer artist radio). In-library matches queue straight onto the **play queue** — cadence-checked during a tempo-locked run, with unstretchable tracks marked **off cadence** — while missing tracks get a grabber-gated **Grab** into the download queue; **Queue all** adds every eligible match at once.

**v2.5.0** — A **Suggestions** page (grabber): suggested artists and tracks to grab next, derived from your library's top and starred artists via the keyless **Deezer public catalog** (no account/key, works even without Spotify connected). One-click add-to-queue; owned artists filtered out (sampled ones badged); dismissals persist. Plus a collapsible **Related** panel on every Artist/Album/Track page (similar artists + tracks, live from Deezer, in-library ones linked; read-only with the grabber off), an **artist detail popup** (bio + top tracks + discography, add a whole album/single or single tracks), and **30-second Deezer previews** (▶) on suggested/related tracks that duck and auto-resume the queue.

**v2.4.4** — A **dislike toggle**, star's opposite number: dislike a track from the run queue, the track page, or the library and it's never picked for a run again — a **Disliked** filter pill in the library lists (and lets you undo) disliked tracks, and the run-queue builder drops them before scoring so they're excluded from the initial queue and every auto-refill.

**v2.4.3** — **Run mode** now fits a single phone screen (scaling cover art, compact presets/steps, a lock icon replacing the pill) with the global player bar hidden on `/run`. A **Queue** view lists the run queue in place — jump to a track, star keepers, per-row `native · octave · stretch → result` math — plus **auto-refill** so a run never goes silent, a **lyrics** button on the transport, and a live tempo re-stretch when you correct a BPM from the track page. Playback resilience: dead streams retry on play, expired sessions route to sign-in instead of looking like a missing file, and iOS lock-screen queue advance is more reliable. BPM tags now write correctly to WAV/AIFF/DSF (ID3-backed) containers, including files with no existing tag header.

**v2.4.2** — Installable **PWA** (home-screen app, opens straight on the Run page; lock-screen/headset **Media Session** controls; no offline caching by design — requires HTTPS in front of the UI). New **Run mode**: a full-screen tempo-run cadence player — target BPM with ±1/±5 steps or four named presets, octave-folded queue building preferring the new **starred tracks**, waveform scrubbing, and a pitch-preserving **tempo lock** that stretches every song onto your step. **Find metadata** fills a track's whole tag set from Spotify/Deezer (by ISRC when known) with a field-by-field review before saving.

**v2.4.1** — Lyrics from LRCLIB (plain + synced LRC; embed or `.lrc` sidecar; per-track, bulk fill, and grabber auto-fetch) with a live-following player lyrics drawer. Image editing for track covers, whole-album covers, and artist images via a Spotify/Deezer picker; opt-in `artist.jpg` save into the library. Deezer API calls now rate-limited; the player bar shows the track's BPM with a beat-pulsing dot.

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
