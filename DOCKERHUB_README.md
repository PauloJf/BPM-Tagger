# BPM Tagger

Auto-detects BPM for every track in your [Navidrome](https://www.navidrome.org/) library, writes it back to the file's metadata, and provides a password-protected **React** web UI for reviewing and correcting results.

Three detectors cross-validate every track — **deeprhythm** (CNN), **essentia** RhythmExtractor2013, and **librosa** — so octave errors are caught automatically and disagreements are flagged for manual review.

**Multi-source playlists:** watch playlists from **Spotify** and **Navidrome**, or build your own **Local** playlists with an **"Add to playlist"** button on any track — reconciled against your library (have / missing / new / removed) and usable as **Run-mode sources**. Navidrome and Local playlists work with just your Navidrome credentials or nothing at all — no grabber. With the **optional Spotify grabber** (`GRABBER_ENABLED=true`) you can also add Spotify playlists by URL and **download the tracks you're missing** (Deezer via your own ARL, yt-dlp fallback), transcode to one format, tag + BPM-analyze, and file them into your library by a path template — with an ambiguity inbox and ntfy pings. A **Suggestions** page recommends artists and tracks to grab next, derived from your library via the keyless Deezer catalog.

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
| `RUN_PASSWORD` | _(empty)_ | Optional **shared, full-access Guest login** for a locked-down **player mode** (Run page + a runner-focused About only). For per-person accounts scoped to specific playlists, create **named player users** in Settings → Player access → Player users. Also settable in Settings → Player access |
| `RUN_SESSION_DAYS` | `30` | How long a player login stays signed in (days) |
| `RUN_FORCE_TEMPO` | `false` | Default for the Run page's "play everything, force tempo" toggle (ignore the BPM tolerance, force every track onto the target; rates clamped) |
| `SYNC_INTERVAL_MINUTES` | `0` | Minutes between automatic background sync passes for playlists (Spotify + Navidrome), star sync, and play-count pulls. `0` = off (manual only); floored to 5. Watch mode only |
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
- **Related & artist explorer** — a collapsible **Related · powered by Deezer** panel on every artist/album/track page (similar artists + tracks, fetched on expand); click any artist for a popup with a short bio, top tracks and full discography (albums + singles/EPs), adding single tracks or a whole album to the download queue. **30-second previews** (▶) on suggested/related rows — and on **inbox candidate cards** (Deezer candidates, plus the source track via ISRC lookup) so you can listen before choosing — play through the ducking player; yt-dlp candidates link to their source page instead. Read-only with the grabber off
- **Queue similar from the player** — a similar-tracks button on the player bar (and **≈ Similar** on the Run page's queue view): in-library matches queue straight onto the **play queue** (cadence-checked during a tempo-locked run — unstretchable tracks show **off cadence**), missing ones get a grabber-gated **Grab**, plus a **Queue all** shortcut
- **Suggestions** _(grabber)_ — a page of artists & tracks to grab next, seeded from your library's top/starred artists via the keyless Deezer catalog; one-click add-to-queue, dismissable, auto-refreshing
- **Run mode** — a full-screen tempo-run player that fits one phone screen: viewport-scaled cover art, big target-BPM readout with the tempo-lock toggle and a `native · stretch × octave → result` breakdown, ±1/±5 steps, four **named presets** or an in-place **queue view**, lyrics drawer, waveform + large transport; a **source picker** builds the queue from your whole library or a specific **playlist** (with an "N of M available" count); auto-queues octave-folded BPM matches (**starred tracks** first, **disliked tracks** never), **refills the queue automatically when the last track starts** (a playlist run prefers its own tracks and **tops up from your library at the same cadence** when the playlist is too small, so it never loops one song), and **locks the tempo** so every song stretches onto your step, pitch preserved; an optional **"play everything, force tempo"** toggle ignores the BPM tolerance so any track can fill the queue (extreme rates clamped); the track's **play count** shows under the title/artist, and the desktop track-info column shows **file audio quality** (format + bit depth / sample rate, or bitrate)
- Duplicate resolution — a dedicated **Duplicates** page; step through groups side-by-side (stacked on mobile) and move unwanted copies to a recoverable **trash** (purged from Settings)
- ISRC lookup (Deezer / Spotify / MusicBrainz) on track detail & compare, plus a **bulk "Fill missing ISRCs"** with a duration-match guard
- **Find metadata** — fill a track's whole tag set from Spotify/Deezer (directly by ISRC when known, else by artist + title or filename), review, then save
- **Re-analyze** button on track detail — re-runs detection for a single track without a full scan
- Save & Lock corrected BPM; Unlock for re-analysis
- Stats — BPM histogram with peak highlight and median marker, detector breakdown, Reviewed card, Retry Errors; a **Run mode** card (tracks played, time on feet, tempo-shifted vs native, average cadence, time per cadence) once you've done a run; with the grabber on, a **Library sources** card (grabbed vs pre-existing, downloads per provider, duplicates / ISRC / playlist-coverage rollups)
- Settings — live config changes without restart; `/healthz` JSON endpoint
- **Appearance** — light/dark toggle in the navbar, plus a **custom accent color** under Settings → Appearance: pick an accent swatch or dial in any hue, and the whole UI (logo, buttons, focus rings, progress bars, login glow + animated bars) recolors instantly, saved per-browser
- **Player mode** _(optional)_ — a locked-down view showing **only the Run page** (plus a runner-focused About): play, star/dislike, and scrobble, but no access to the library, settings, or downloads (enforced server-side, default-deny). Sign in with the shared **Guest login** (`RUN_PASSWORD`) **or a named player user** (optional username field) created in **Settings → Player access → Player users** — each player user is **always scoped to specific playlists** (run one, or all of them pooled via **All my music**; the server refuses the library/starred pool and any other playlist), while the shared Guest login is full-access. Reset/disable/delete a user and their sessions end at once; the Guest login stays a full-access shared login, so upgrades change nothing. Ideal for a shared phone/tablet or a dedicated running device; player logins stay signed in far longer (`RUN_SESSION_DAYS`, default 30). On desktop it keeps a slim sidebar with just **Run** + **About**; on phones that collapses to a compact top bar with the same tabs, and Run uses a one-screen layout with the waveform/transport pinned to the bottom
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

**v2.7.2** — **Admin two-factor + hardened login.** The admin login can now require a 6-digit **authenticator-app code (TOTP)** on top of the password — enable it under **Settings → Two-factor**, with one-time recovery codes and a `MODE=disable_2fa` escape hatch (implemented with the standard library, no new dependency; admin only). **Brute-force protection is now layered**: on top of the existing per-IP limit, failed logins are throttled **per account** (catching a distributed attack on one identity) and **globally** (a broad-sweep backstop) — see the new `UI_ACCOUNT_MAX_LOGIN_ATTEMPTS` / `UI_GLOBAL_MAX_LOGIN_ATTEMPTS` / `UI_GLOBAL_LOCKOUT_SECONDS`. A **Settings → Login protection** panel shows the active thresholds and can clear a stuck lockout. Also: two **iPhone-only Run fixes** — the cover art shows again (it was collapsing on iOS Safari) and the extra space below the transport is gone (Android was already correct).

**v2.7.1** — **Run-mode auto-advance, hardened.** A batch of player fixes so tracks reliably advance under every network and playback condition — especially on mobile and under a tempo lock: the player **retries the boundary `play()`** when the browser loads the next track but won't start it, **stops the "connecting" reload loop**, **falls back to streaming** when a preloaded blob won't decode, makes advance **mirror the full rebuild path** (fixing an Android boundary stall), **disables blob preload** (it broke advance under a tempo lock), and **recovers from transient boundary errors** instead of stopping — plus live buffer diagnostics and an end-to-end auto-advance test guard. Also: scoped players get an **"All my music"** pooled run source, a **run-queue leak** where scoped players could top up from the whole library is fixed, and **Player access** is split into two clear roles — **Guest login** (shared `RUN_PASSWORD`) and always-scoped **Player users**.

**v2.7.0** — **Per-user run access + more.** Named **run users** join the shared `RUN_PASSWORD`: create accounts under **Settings → Player Access → Run users**, each full-access or **scoped to specific playlists**, and sign in with the new optional **username** field (blank = admin or the shared guest, as before). The existing run password stays a full-access guest, so upgrades change nothing. Plus: a **"play everything, force tempo"** Run toggle (drop the BPM tolerance, force every track onto your target), **"queue missing" for Navidrome playlists** (grab them by metadata), and an optional **background sync scheduler** (`SYNC_INTERVAL_MINUTES`) that runs playlist/star/play-count sync on a timer. Also: **Local playlists + "Add to playlist"** — build a playlist by hand from library tracks (on-disk, so instantly **have**), usable as a Run-mode source and `.m3u`-exportable like any other. And **inbox candidate previews** — a ▶ on each Deezer candidate (and on the source track, via ISRC) plays a 30-second clip through the ducking player before you choose; yt-dlp candidates link to their source page.

**v2.6.14** — **Everything links into the library + layout audit.** Completed downloads in the grabber **Queue** now link their **title/artist/album** to the library's track/artist/album pages; the **track page links its album**; Add Music's **✓ in library** chip opens the matching track. A full breakpoint audit (every page, 320→1280px) fixed a long-standing collision where Tailwind's `.container` **capped every page at 768px on mid-size windows** (and broke the Run cockpit around 1000px), plus sideways scrolling on the Settings page at narrow-desktop widths, the Library **pagination** row on phones, and playlist-card cover/actions alignment.

**v2.6.13** — **One-screen Run layout + Stats polish.** The phone Run page is now a fixed-height flex layout: the **cover flexes to the leftover height** and the page **never scrolls** — native-player feel in browser and installed PWA, safe areas and iOS/Android accounted for by construction (no more hand-tuned viewport math). Play count is desktop-only on the Run page. Stats: **Most played** now pages — 15 rows per list with **Show more** for the rest — and a long title/artist no longer forces the phone page to scroll sideways.

**v2.6.12** — **Run-mode & PWA fixes.** **Signing out now stops playback** (audio no longer keeps playing behind the login screen of the installed PWA; a session expiry still restores the queue after signing back in). The **admin mobile Run layout fits one phone screen again** (it now reserves the top bar's height, so the transport doesn't spill off the bottom). **Status notes** (Buffering/Offline/stream errors, stale-queue and source-change notices) now show one at a time as a pill **floating just above the waveform** — out of the layout flow, so they can't push the transport off-screen. And on slow links, the Run look-ahead is no longer discarded at stalls/track boundaries, fixing **iOS background auto-advance** — prefetched tracks stay in memory so the queue keeps going with the phone locked.

Older releases — the full history lives in [CHANGELOG.md](https://github.com/PauloJf/BPM-Tagger/blob/main/CHANGELOG.md) on GitHub.
