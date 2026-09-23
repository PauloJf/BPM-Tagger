# BPM Tagger

**Cadence-synced running music from your own library.** BPM Tagger started as a tagger — auto-detect the BPM of every track in your [Navidrome](https://www.navidrome.org/) library and write it back to the file's metadata — and grew into what those tags make possible: a **tempo-locked running player**, a full **library companion**, and an optional **downloader**, all behind a password-protected **React** web UI.

Three detectors cross-validate every track — **deeprhythm** (CNN), **essentia** RhythmExtractor2013, and **librosa** — so octave errors are caught automatically and disagreements are flagged for manual review.

One container, four jobs:

- **The BPM engine** — three-detector analysis, octave correction, tag writing, review queue, manual locks
- **Run — the cadence player** — pick a step cadence and run to your own music: octave-folded queue building (starred tracks first), a pitch-preserving tempo lock, offline preloading for network dead zones, and a locked-down player/kiosk login; **Listen** is the regular non-cadence player
- **The library companion** — playlists (Spotify / Navidrome / Local) with compare / merge / split operations and per-playlist stats, lyrics, cover & artist art, duplicate resolution, ISRCs, and two-way star sync + scrobbling back to Navidrome
- **The music grabber** *(optional, off by default)* — watch your own Spotify playlists and download what's missing (Deezer via your own ARL → yt-dlp fallback), transcoded, tagged, BPM-analyzed, and filed by a path template

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
| `PRESERVE_MTIME` | `true` | Keep each file's modified time after any write (tags, lyrics, metadata, cover art) |
| `COMPUTE_WAVEFORMS` | `auto` | Waveform peaks for the player; `auto` = only when the web UI is on |
| `WORKERS` | `1` | Parallel analysis threads (+~500 MB RAM each for deeprhythm) |
| `REFRESH_HASHES` | `false` | Recompute hashes before scanning (migration from pre-1.0.0) |
| `BPM_MIN` / `BPM_MAX` | `60` / `200` | BPM floor and ceiling — values below are doubled, above are halved |
| `USE_DEEPRHYTHM` | `false` | CNN detector (~500 MB/worker); **only effective on `:full`** — ignored on `:latest` |
| `USE_ESSENTIA` | `true` | Essentia RhythmExtractor2013 detector |
| `OCTAVE_CORRECTION` | `true` | Auto-fix 2× BPM errors between detectors |
| `MEASURE_LOUDNESS` | `true` | Measure loudness (LUFS) during the scan; existing ReplayGain tags are reused |
| `NORMALIZE_PLAYBACK` | `true` | Level playback volume so loud masters don't jump out |
| `LOUDNESS_TARGET_LUFS` | `-14` | Target playback loudness in LUFS (range -30…-5) |
| `REVIEW_DISAGREE_THRESHOLD` | `15` | BPM gap that flags a track for review |
| `ENABLE_UI` | `false` | Start the web UI |
| `UI_PASSWORD` | _(empty)_ | Web UI password — **required** when UI is enabled |
| `UI_USERNAME` | _(empty)_ | Optional admin username (for password managers); blank keeps password-only sign-in |
| `UI_SESSION_HOURS` | `24` | Admin session length — signed out after this many idle hours (sliding) |
| `RUN_PASSWORD` | _(empty)_ | Optional **shared Guest login** for locked-down player mode. For per-person accounts scoped to playlists, create **named player users** in Settings → Player access |
| `RUN_SESSION_DAYS` | `30` | How long a player login stays signed in (days) |
| `PLAYER_LISTEN_MODE` | `off` | What player logins get besides Run: `off` · `on` adds Listen · `default` lands on Listen · `only` pure jukebox. Named users can override |
| `RUN_STRETCH_LIMIT_PCT` | `15` | How far (%) a track may be sped up or slowed to reach the cadence — decides both queue eligibility and playback clamp |
| `SYNC_INTERVAL_MINUTES` | `0` | Minutes between background sync passes (playlists, stars, play counts). `0` = manual only; floored to 5. Watch mode only |
| `INSTALL_PING` / `INSTALL_PING_URL` | _(ask on first run)_ | Opt-in anonymous install ping (version only; no identifier/data/cookies) |
| `NTFY_TOPIC` | _(empty)_ | ntfy topic (leave empty to disable) |
| `NAVIDROME_URL` | _(empty)_ | Trigger Navidrome rescan after each scan |
| `NAVIDROME_STAR_SYNC` | `false` | Two-way star sync toggle (Settings → Navidrome) |
| `NAVIDROME_SCROBBLE` | `false` | Scrobble built-in-player plays to Navidrome (Settings → Navidrome) |
| `SUBSONIC_ENABLED` | `false` | Serve the Subsonic API at `/rest` for Subsonic apps; own credentials in Settings → Subsonic API |
| `SUBSONIC_TRANSCODE` | `false` | Let Subsonic apps request Opus/MP3 at a lower bitrate (ffmpeg, on the fly) |
| `LYRICS_ENABLED` | `false` | Auto-fetch lyrics (LRCLIB) for grabbed tracks; manual/bulk fetch always available in the UI |
| `LYRICS_MODE` | `embed` | Store lyrics in the file tag (`embed`) or as a `.lrc` sidecar (`sidecar`) |

All variables documented in [`docker-compose.yml`](https://github.com/PauloJf/BPM-Tagger/blob/main/docker-compose.yml).

---

## Web UI

Set `ENABLE_UI: "true"` and a strong `UI_PASSWORD`, then open `http://your-host:5000`.

> **⚠️ LAN access only by default.** The UI runs over plain HTTP. Place a reverse proxy (nginx, Caddy) with TLS in front of port 5000 before exposing it outside your local network — and set `UI_TRUSTED_PROXIES` to the number of proxies so the login lockout keys on the real client IP.

The UI password is stored as a salted hash once changed in **Settings** (never plaintext), a password change logs out all other devices, and `settings.json` is written `0600`.

**Library** — sortable table with BPM, confidence and detector, live search, BPM ± tolerance filter, and filter pills (Starred / Disliked / Review / Locked / No ISRC / No playlist / Deleted). Tracks | Artists | Albums browse views with cover art, per-artist and per-album pages, and a shared **Play / Shuffle / Add to queue** trio everywhere. Duplicate resolution with a recoverable trash, bulk ISRC fill, and **Find metadata** to fill a track's whole tag set from Spotify/Deezer.

**Run mode** — a full-screen tempo-run player that fits one phone screen: big target-BPM readout with the tempo-lock toggle and a `native · stretch × octave → result` breakdown, four named presets, source picker (whole library or a playlist), and a queue that auto-refills before the last track ends. Starred tracks come first, disliked never, and every song is stretched onto your cadence with pitch preserved. Save a run queue as a playlist; the **Cadence** page answers "what can I run at 165?" by the same rule.

**Listen** — the regular non-cadence player: play any playlist in order or shuffled at native speed (no BPM required), with a **radio** toggle that keeps refilling from the same playlist.

**Player** — the persistent player bar carries a drag-to-reorder queue, waveform scrubbing, tap-tempo, keyboard shortcuts, and a reload-persistent queue that **follows your account across devices**. Pop it out as a **floating mini player** (Document Picture-in-Picture), or open the **lyrics drawer** for synced LRC lyrics that follow along — click a line to seek.

**Playlists** — watch **Spotify** and **Navidrome** playlists or build your own **Local** ones, reconciled against your library (have / missing / new / removed) and usable as Run sources. **Compare** two, **Merge** several, or **Split** one by cadence or artist — all outputs Local, nothing ever writes back. Each detail page opens with a stats strip (runtime, BPM histogram, plays, per-preset runnable counts). Covers, descriptions, pinning, search, sort and drag-reorder included.

**Navidrome integration** — two-way **star sync**, opt-in **scrobbling** at the halfway mark (reaching Last.fm/ListenBrainz through it), and **play-count import** usable as a "prefer familiar tracks" run preference. Your BPM tags also power Navidrome **smart playlists** (`.nsp` with a `bpm` range).

**Artwork & lyrics** — embedded covers everywhere, album-wide cover setting, custom artist images (or opt-in Deezer fetching, optionally saved as `artist.jpg` for Navidrome), and LRCLIB lyrics per track or in bulk, stored embedded or as sidecars.

**Grabber extras** *(optional)* — a **Suggestions** page of artists and tracks to grab next from the keyless Deezer catalog, a **Related** panel on every artist/album/track page with 30-second previews, and an ambiguity inbox for low-confidence matches.

**Player mode** — a locked-down view showing only Run (and optionally Listen), enforced server-side. Sign in with the shared **Guest login** (`RUN_PASSWORD`) or a **named player user** scoped to specific playlists. Ideal for a shared phone or a dedicated running device.

**PWA & offline** — installable to your home screen, or as a desktop app from Chrome/Edge (needs HTTPS), with lock-screen and media-key controls. The player preloads the next few queue tracks into a capped per-device cache, and per-preset **Prepare offline** chips download whole cadence queues in advance — so a run survives network dead zones.

**Also** — BPM review queue, per-track re-analyze, save & lock, Stats page (BPM histogram, detector breakdown, run totals, library sources), light/dark toggle with a custom accent colour saved to your account, live settings changes without restart, and a `/healthz` endpoint.

Full feature detail: [README on GitHub](https://github.com/PauloJf/BPM-Tagger#readme).

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

**v2.17.1** — `PRESERVE_MTIME` is now respected by the metadata editor, ISRC writes and cover embedding, not just BPM and lyrics. The update check compares versions numerically, so a build ahead of the newest release no longer reports itself out of date.

**v2.17.0** — Beat-paced equalizer bars in the mini player and on the current queue row, ticking to the locked cadence in Run mode and the track's native BPM otherwise.

**v2.16.0** — The admin login can have an optional **username**, so password managers can save and autofill a proper credential pair. Signing in with a blank username keeps working.

Every release: [GitHub Releases](https://github.com/PauloJf/BPM-Tagger/releases) · full history: [CHANGELOG.md](https://github.com/PauloJf/BPM-Tagger/blob/main/CHANGELOG.md)
