# BPM Tagger

Auto-detects BPM for every track in your [Navidrome](https://www.navidrome.org/) library, writes it back to the file's metadata, and provides a password-protected **React** web UI for reviewing and correcting results.

Three detectors cross-validate every track — **deeprhythm** (CNN), **essentia** RhythmExtractor2013, and **librosa** — so octave errors are caught automatically and disagreements are flagged for manual review.

**Multi-source playlists:** watch playlists from **Spotify** and **Navidrome**, or build your own **Local** playlists with an **"Add to playlist"** button on any track (or copy a whole playlist's owned tracks into a local one with **"Add all to playlist…"**) — reconciled against your library (have / missing / new / removed), playable straight from their detail page (**▶ Play** / **⇄ Shuffle** / **+ Add to queue**, over the tracks you own), and usable as **Run-mode sources**. Playlist rows show **cover art** (your file's embedded art, else the source's), and Local playlists get a cover of their own — pick/paste/upload one, or let it build a **2×2 collage** from its tracks. Give any playlist a **description** and **pin** it to the top of the list (both survive a sync); **rename** your Local ones. Inside a playlist you can **search and sort** (playback follows what's on screen), spot **duplicate** rows, and **drag-reorder** a Local one. A run queue can be **saved as a playlist** straight from the Run page. A **Cadence** page answers "what can I run at 165?" by the exact run-queue rule — play it, save it, or open it in Run — and playlist cards show per-preset runnable counts. Navidrome and Local playlists work with just your Navidrome credentials or nothing at all — no grabber. With the **optional Spotify grabber** (`GRABBER_ENABLED=true`) you can also add Spotify playlists by URL and **download the tracks you're missing** (Deezer via your own ARL, yt-dlp fallback), transcode to one format, tag + BPM-analyze, and file them into your library by a path template — with an ambiguity inbox and ntfy pings. A **Suggestions** page recommends artists and tracks to grab next, derived from your library via the keyless Deezer catalog.

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
| `MEASURE_LOUDNESS` | `true` | Measure loudness (LUFS) during the scan; existing ReplayGain tags are reused |
| `NORMALIZE_PLAYBACK` | `true` | Level playback volume so loud masters don't jump out |
| `LOUDNESS_TARGET_LUFS` | `-14` | Target playback loudness in LUFS (range -30…-5) |
| `REVIEW_DISAGREE_THRESHOLD` | `15` | BPM gap that flags a track for review |
| `ENABLE_UI` | `false` | Start the web UI |
| `UI_PASSWORD` | _(empty)_ | Web UI password — **required** when UI is enabled |
| `UI_SESSION_HOURS` | `24` | Admin session length — signed out after this many idle hours (sliding) |
| `RUN_PASSWORD` | _(empty)_ | Optional **shared, full-access Guest login** for a locked-down **player mode** (Run page + a runner-focused About only). For per-person accounts scoped to specific playlists, create **named player users** in Settings → Player access → Player users. Also settable in Settings → Player access |
| `RUN_SESSION_DAYS` | `30` | How long a player login stays signed in (days) |
| `PLAYER_LISTEN_MODE` | `off` | What player logins get besides Run mode: `off` Run-only kiosk · `on` adds a **Listen** tab (regular playlist player) · `default` Listen is also the landing page · `only` pure jukebox (no Run). Also settable in Settings → Player access |
| `RUN_STRETCH_LIMIT_PCT` | `15` | How far (%) a track may be sped up or slowed down to reach the target cadence — the single rule behind a run queue: tracks that can't get there aren't queued, and playback is clamped to the same bound. Also settable in Settings → Run Mode |
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
- Library table with BPM, confidence, detector info, filter pills (All / Starred / Disliked / Review / Locked / No ISRC / **No playlist** / **Deleted**), and `pending` badge before analysis; **live search** as you type; **BPM ± tolerance filter**; error badge tooltips; back navigation restores filter/page/search state. The **No playlist** pill lists tracks not in *any* playlist (Spotify, Navidrome, or Local)
- **Artists & Albums browse views** — a Tracks | Artists | Albums switcher with filterable card grids (track counts, years, average BPM) linking into the per-artist/per-album pages
- **Cover art everywhere** — embedded covers on library rows, browse cards, artist/album pages and track detail, with a show/hide toggle and cached delivery
- **Artist images** — a custom image you pick, local `artist.jpg` (Navidrome convention), or opt-in, rate-limited fetching from Deezer's public API (`FETCH_ARTIST_IMAGES`), cached on disk; falls back to album art. `ARTIST_IMAGES_TO_LIBRARY` saves fetched/picked images as `artist.jpg` in the artist's folder so Navidrome sees them too
- **Image editing** — change a track's cover, set an album cover across all its tracks at once, or pick a custom artist image — searching Spotify/Deezer, pasting a URL, or uploading a file
- **Volume levelling** — each track's loudness (LUFS, EBU R128) is measured during the scan (existing **ReplayGain** tags reused), and the player attenuates loud masters to a configurable target (default **-14 LUFS**) so nothing jumps out mid-run; your volume slider still applies on top
- **Lyrics** — plain or synced (LRC) lyrics from LRCLIB per track or in bulk, viewable/editable on the track page, stored embedded or as `.lrc` sidecars (Navidrome-compatible); a **player lyrics drawer** follows synced lyrics live (click a line to seek), steps plain lyrics manually, has an **S / M / L / XL** text-size stepper, and can be **maximized or drag-resized**
- **Navidrome star sync** — two-way: stars set here push to Navidrome as favourites, stars set in Navidrome pull in (feeding Run mode's starred preference); per-track baseline merge, path + fuzzy matching, manual **Sync stars now** trigger
- **Navidrome scrobbling & play counts** — opt-in: built-in-player plays (Run mode included) scrobble to Navidrome at the halfway mark (reaching Last.fm/ListenBrainz through it); **Pull play counts** imports Navidrome's play counts, shown per track and usable as a **prefer familiar tracks** run-queue preference. Your BPM tags also power Navidrome **smart playlists** (`.nsp` with a `bpm` range) for cadence playlists in any Subsonic client
- BPM Review queue — Prev/Next navigation, Approve without re-analysing; approved/locked tracks marked `reviewed` and removed from queue
- Audio player with real waveform scrubbing and tap-tempo (Space bar)
- Persistent player bar with **Play all / Shuffle** queueing (labelled by tag title, not filename), prev/next, repeat, volume, a **queue viewer** showing cover art + BPM per row with **drag-to-reorder** (↑/↓ too), **Clear**, an **S/M/L/XL text-size stepper**, and **maximize / drag-resize**, a reload-persistent queue that resumes at the saved position, keyboard shortcuts (`k` play/pause, `q` queue, `l` lyrics, arrows, volume, mute), and a ducking **preview** from detail/compare views
- **Floating mini player** — pop the now-playing card out into an always-on-top window (Document Picture-in-Picture, Chromium desktop) that survives tab/route changes: the **cover art fills the blurred background** behind a glass card, title/artist, seekable progress, prev/play/next, volume, and the Run-mode tempo-lock pill. **Click the Run page cover** to pop it out (button also in the player bar); as an installed PWA it tucks the main window away where the runtime allows; playback stays in the main tab and the window follows the app theme
- **Artist & album pages** and a cadence ½×/2× BPM filter for running
- **Related & artist explorer** — a collapsible **Related · powered by Deezer** panel on every artist/album/track page (similar artists + tracks, fetched on expand); click any artist for a popup with a short bio, top tracks and full discography (albums + singles/EPs), adding single tracks or a whole album to the download queue — the same catalog popup opens from a **Browse Deezer** button on every library artist page. **30-second previews** (▶) on suggested/related rows — and on **inbox candidate cards** (Deezer candidates, plus the source track via ISRC lookup) so you can listen before choosing — play through the ducking player; yt-dlp candidates link to their source page instead (inbox cards collapse to one-line summaries — candidate count + best score — with **Expand all / Collapse all** for quick triage). Read-only with the grabber off
- **Queue similar from the player** — a similar-tracks button on the player bar (and **≈ Similar** on the Run page's queue view): in-library matches queue straight onto the **play queue** (cadence-checked during a tempo-locked run — unstretchable tracks show **off cadence**), missing ones get a grabber-gated **Grab**, plus a **Queue all** shortcut
- **Suggestions** _(grabber)_ — a page of artists & tracks to grab next, seeded from your library's top/starred artists via the keyless Deezer catalog; one-click add-to-queue, dismissable, auto-refreshing. A **local playlist's detail page** also has its own admin-only Suggestions panel — Deezer picks seeded from that playlist's most-frequent artists, adding in-library matches straight to the playlist (missing ones via the download queue)
- **Run mode** — a full-screen tempo-run player that fits one phone screen: viewport-scaled cover art, big target-BPM readout with the tempo-lock toggle and a `native · stretch × octave → result` breakdown, ±1/±5 steps, four **named presets** or an in-place **queue view**, lyrics drawer, waveform + large transport; a **source picker** builds the queue from your whole library or a specific **playlist** (with an "N of M available" count); auto-queues octave-folded BPM matches (**starred tracks** first, **disliked tracks** never), **refills the queue automatically when the last track starts** (a playlist run prefers its own tracks and **tops up from your library at the same cadence** when the playlist is too small, so it never loops one song), and **locks the tempo** so every song stretches onto your step, pitch preserved; a single **max stretch** setting decides both which tracks are eligible and how far they're pulled; the track's **play count** shows under the title/artist, and the desktop track-info column shows **file audio quality** (format + bit depth / sample rate, or bitrate)
- **Listen** — the regular (non-cadence) player: a full-screen now-playing page with cover art, seekable waveform, star/dislike, shuffle/repeat/volume and the drag-to-reorder queue; **play any playlist** in order or shuffled at native speed (no BPM required — un-analyzed tracks play too), and a **radio** toggle keeps the queue refilling from the same playlist as it nears its end. Optionally available to **player logins** via `PLAYER_LISTEN_MODE` (off / on / default / only — up to a pure jukebox kiosk with no Run page), enforced server-side
- Duplicate resolution — a dedicated **Duplicates** page; step through groups side-by-side (stacked on mobile) and move unwanted copies to a recoverable **trash** (purged from Settings)
- ISRC lookup (Deezer / Spotify / MusicBrainz) on track detail & compare, plus a **bulk "Fill missing ISRCs"** with a duration-match guard
- **Find metadata** — fill a track's whole tag set from Spotify/Deezer (directly by ISRC when known, else by artist + title or filename), review, then save
- **Re-analyze** button on track detail — re-runs detection for a single track without a full scan
- Save & Lock corrected BPM; Unlock for re-analysis
- Stats — BPM histogram with peak highlight and median marker, detector breakdown, Reviewed card, Retry Errors; a **Run mode** card (tracks played, time on feet, tempo-shifted vs native, average cadence, time per cadence) once you've done a run; with the grabber on, a **Library sources** card (grabbed vs pre-existing, downloads per provider, duplicates / ISRC / playlist-coverage rollups)
- Settings — live config changes without restart; `/healthz` JSON endpoint
- **Appearance** — light/dark toggle in the navbar, plus a **custom accent color** under Settings → Appearance: pick an accent swatch or dial in any hue, and the whole UI (logo, buttons, focus rings, progress bars, login glow + animated bars) recolors instantly. Your accent is **saved to your account** so it follows you across browsers and devices (the shared Guest login stays per-browser)
- **Player mode** _(optional)_ — a locked-down view showing **only the Run page** (plus a runner-focused About): play, star/dislike, and scrobble, but no access to the library, settings, or downloads (enforced server-side, default-deny). Sign in with the shared **Guest login** (`RUN_PASSWORD`) **or a named player user** (optional username field) created in **Settings → Player access → Player users** — each player user is **always scoped to specific playlists** (run one, or all of them pooled via **All my music**; the server refuses the library/starred pool and any other playlist), while the shared Guest login is full-access. Reset/disable/delete a user and their sessions end at once; the Guest login stays a full-access shared login, so upgrades change nothing. Ideal for a shared phone/tablet or a dedicated running device; player logins stay signed in far longer (`RUN_SESSION_DAYS`, default 30). On desktop it keeps a slim sidebar with just **Run** + **About** (plus **Listen** when `PLAYER_LISTEN_MODE` grants it — up to a Listen-only jukebox); on phones that collapses to a compact top bar with the same tabs, and Run uses a one-screen layout with the waveform/transport pinned to the bottom
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

**v2.11.0** — **Cadence views, playlist artwork, and playlists you can actually manage.** A new **Cadence** page answers "what can I run at 165?" using the *exact* rule the run queue uses (octave fold + your max stretch), listing every eligible track with its `native → folded ×rate` — play it, save it to a playlist, or **Open in Run** to run to that cadence. The Playlists page gains a **preset strip** with live library-ready counts, and each card shows quiet per-preset counts (`155:11 · 165:8`) that start a run at that cadence with that playlist as the source. Playlist rows now show **cover art**, and **Local playlists get covers of their own** — pick, paste or upload one, or let the server build a **2×2 collage** from the playlist's tracks. Plus: **rename** (Local only — a synced name reverts on sync), **description** and **pin** (both survive a sync, on any source), **drag-reorder** a Local playlist, **sort and search** within one (playback follows what you see), **duplicate detection**, and a **Save…** action that turns the run queue you just finished into a playlist.

**v2.10.0** — **Run settings: three knobs become one.** ⚠️ **Breaking:** `RUN_TOLERANCE_PCT` and `RUN_FORCE_TEMPO` are gone, along with the Run page's "play everything, force tempo" toggle. **Max stretch** (`RUN_STRETCH_LIMIT_PCT`, default 15%) is now the single authority over a run queue: a track is queued only if it can reach your cadence within that limit, and playback is clamped to the same bound. Match tolerance and max stretch always measured the same quantity in the same units, and which one actually filtered your queue flipped depending on the force toggle — so one slider now does both jobs. If you had tolerance tuned tight (say 1%), expect a **wider pool** after upgrading; lower Max stretch to tighten it again. Stale env vars and settings entries are ignored, not errors.

**Also in v2.10.0** — **playlists are playable, and a new queue ends a run.** Any playlist's detail page gets **▶ Play**, **⇄ Shuffle** and **+ Add to queue** over the tracks you actually own, following the status tab you're on and volume-levelled like the rest of the library. Starting a queue anywhere now **releases the tempo lock and stops the mid-run auto-refill** — fixing a live bug where hitting Play on an album mid-run left it stretched onto your cadence and quietly padded with tracks from the *previous* run's source (adding to the queue, and previews, still leave a run alone). And **"Enqueue missing" is now "Download missing"** — with a play queue on the same page, "queue" meant two different things; the endpoint is unchanged.

**v2.9.0** — **Volume levelling.** Every track's perceived loudness is now measured during the BPM scan (integrated LUFS, ITU-R BS.1770 / EBU R128 — the same measure streaming services level to), and the player uses it to bring loud masters down to a **target loudness** so one hot track doesn't blast you mid-run. Files that already carry a **ReplayGain tag** are read instead of re-measured, so an already-tagged library costs nothing extra. Levelling only ever turns tracks *down* (the HTML audio element has no headroom above full volume), quieter and unmeasured tracks play untouched, and your volume slider stays yours — the levelling multiplies on top of it. **Settings → Playback** has the on/off toggle, the target (default **-14 LUFS**), a measure-during-scans toggle, and a **Measure missing loudness** back-fill for libraries scanned before this existed; a track's own page shows its loudness and can re-measure it on demand.

**v2.8.0** — **Copy playlists into local ones + a "No playlist" filter.** Any playlist's detail page (Spotify, Navidrome, or another Local) gets an **"Add all to playlist…"** action that copies every library-backed track into a local playlist you pick or create inline — duplicates skipped, unowned tracks reported as *not in library*, safe to repeat. The **"Add to playlist"** button now also sits on each **have** row of a playlist page for cherry-picking one track. A new **"No playlist"** library filter (with a live count) surfaces tracks that aren't in *any* playlist. The player **queue drawer** gains the **S/M/L text-size stepper** the lyrics drawer had, and both drawers get a larger **XL** step. Also fixes long artist names overflowing the player bar, and the playlist detail header rendering blank **have / missing** chips and hiding the **Export .m3u** button.

**v2.7.4** — **Player drawers grow up + per-account accent.** The queue and lyrics popovers can now be **maximized** and **drag-resized** (size remembered per browser; full-width bottom sheet on phones). Queue rows show **cover art + BPM** and support **drag-to-reorder** (↑/↓ still there); the lyrics drawer gets an **S / M / L** text-size stepper; and `q` / `l` join the keyboard shortcuts. Queued tracks are labelled by their **tag title, not the filename**. Local playlists gain an admin-only **Suggestions** panel (Deezer picks from the playlist's own artists). And your **custom accent colour now follows your account** across browsers and devices, not just one browser.

Older releases — the full history lives in [CHANGELOG.md](https://github.com/PauloJf/BPM-Tagger/blob/main/CHANGELOG.md) on GitHub.
