# Reddit launch kit — BPM Tagger v2.6

Copy-paste posts for the v2.6 relaunch. The May 2026 launch already ran in
r/IMadeThis, r/selfhosted, r/navidrome, r/running and r/homelab, so most of these
are framed as a **major update**, not a first reveal.

**Suggested order:** r/selfhosted → r/navidrome → r/opensource → r/musichoarder.
Space posts 1–2 days apart — don't fire the same link across subs within minutes
(spam filters), and keep to Reddit's ~10% self-promotion etiquette by commenting
and participating between launches.

Screenshots referenced below live in [`docs/screenshots/`](screenshots/). Attach the
full-resolution PNGs when posting.

---

## 1. r/selfhosted — major update · top pick

**Title**

> BPM Tagger v2.6 — self-hosted BPM detection for Navidrome, now with a cadence-run player, Spotify grabber, and two-way scrobble/star sync

**Body**

> A while back I shared BPM Tagger here — a small Docker service that scans a Navidrome library, detects each track's BPM with three detectors (deeprhythm CNN + essentia + librosa), writes it back to the file tag, and gives you a web UI to review/correct the results. It's grown a lot since, so here's what's new.
>
> **Run mode** 🏃 — the reason I built the whole thing. I run to cadence, so there's now a full-screen tempo player: pick a target BPM (or a preset like Easy 155 / Tempo 175), and it queues tracks whose BPM matches — octave-folded, so a 75 BPM song serves a 150 cadence at native speed — preferring your starred tracks. A **tempo lock** time-stretches every song onto your exact step, pitch preserved, so your feet stay on the beat. Installs as a PWA that opens straight on the player, with lock-screen controls.
>
> **Music Grabber** (optional) — watch your own Spotify playlists, reconcile them against your library by ISRC/fuzzy match, and auto-download what's missing (Deezer via your own ARL, yt-dlp fallback), transcode, tag + BPM-analyze, and file it by a path template.
>
> **Deeper Navidrome integration** — two-way star sync, opt-in scrobbling (play counts + "last played" stay accurate everywhere, and reach Last.fm/ListenBrainz if Navidrome forwards there), and play-count pull that can bias your run queue toward familiar tracks. Plus BPM tags power native Subsonic **smart playlists**.
>
> Slim image is ~400 MB (essentia + librosa); a `:full` image adds the deeprhythm CNN. Everything's env-var configured via `docker-compose`. AGPLv3, no telemetry beyond a one-time opt-in install ping.
>
> Repo: https://github.com/PauloJf/BPM-Tagger · Docker: `gatoserio/bpm-tagger`
>
> Happy to answer anything — built by a runner scratching his own itch.

**Attach as gallery (this order):**
`02-library.png` → `11-player-desktop.png` → `12-player-mobile.png` → `05-track-detail.png` → `06-stats.png`

**Rule:** Self-promo is fine for devs here — be transparent and reply in comments. Weekends get more traction.

---

## 2. r/navidrome — update · top pick

**Title**

> BPM Tagger v2.6: auto-BPM-tagging + a cadence-run player, now with two-way star sync, scrobbling, and smart-playlist support

**Body**

> For anyone who saw my earlier post — BPM Tagger has picked up a bunch of Navidrome-specific features:
>
> - **Two-way star sync** — stars in BPM Tagger push to Navidrome as favourites, and stars from any Subsonic client pull back in (per-track baseline merge, so "starred here / un-starred there" never get confused).
> - **Scrobbling** (opt-in) — tracks played in the built-in player scrobble to Navidrome past the halfway mark, so play counts + "last played" stay accurate across all your clients, and forward to Last.fm/ListenBrainz.
> - **Pull play counts** — imports Navidrome's counts per track (Navidrome is the source of truth), shown on the track page and usable to bias the run queue toward familiar tracks.
> - **Smart playlists** — the written `bpm` tags mean you can drop in an `.nsp` with a BPM range (plus a half-time range mirroring the octave folding) and get a native cadence playlist in *any* Subsonic client, no BPM Tagger running.
> - Auto-rescan via the Subsonic API after each scan so new tags show up immediately.
>
> Core is still: three-detector BPM analysis (deeprhythm/essentia/librosa) → tag write → review UI. Docker, AGPLv3.
>
> https://github.com/PauloJf/BPM-Tagger

**Attach as gallery (this order):**
`04-review.png` → `05-track-detail.png` → `11-player-desktop.png`

**Rule:** Small and perfectly on-topic — basically no friction.

---

## 3. r/opensource — new audience · strong fit

**Title**

> BPM Tagger — AGPLv3 self-hosted BPM detection + tagging for your music library, with a tempo-run player

**Body**

> I've been building an open-source tool for my self-hosted music setup and wanted to share it here.
>
> **BPM Tagger** scans a music library (built around Navidrome, but it just writes standard BPM tags so any player benefits), detects each track's tempo using three detectors — a PyTorch CNN (deeprhythm), essentia, and librosa — reconciles them with octave-error correction, and writes the result back to the file. A React + Flask web UI lets you review and correct anything the detectors disagreed on.
>
> The feature I actually built it for: a **cadence-run player** that queues songs matching a target BPM and pitch-preservingly time-stretches them onto your exact step, so the music matches your running cadence.
>
> Stack: Python (Flask/Waitress backend), React + Vite + TypeScript + Tailwind frontend, SQLite. Multi-stage Docker build. Full CI (ruff + pytest, tsc + build). AGPL-3.0-or-later.
>
> Repo: https://github.com/PauloJf/BPM-Tagger
>
> Feedback and contributions welcome.

**Attach (1–2 is plenty):**
`02-library.png` → `11-player-desktop.png`

**Rule:** Must be genuinely open — AGPLv3 qualifies. Lead with the repo/license, keep it text-forward.

---

## 4. r/musichoarder — new audience · good fit

**Title**

> Built a self-hosted tool that BPM-tags an entire local library (3 detectors), plus optional Spotify-playlist → local-file grabbing

**Body**

> If you keep a big local library, you know how spotty BPM tags are. BPM Tagger walks the whole library, runs three BPM detectors per track (deeprhythm CNN + essentia + librosa), reconciles them with octave-error correction, and writes a clean `BPM` tag back to the file (MP3/FLAC/OGG/Opus/M4A + more). Everything's tracked in SQLite so re-runs only touch new/changed files, and there's a web UI to review the ones the detectors disagreed on.
>
> There's also an optional **grabber**: point it at your own Spotify playlists, it reconciles them against what's already on disk (ISRC or fuzzy match), downloads only what you're missing (Deezer via your own ARL, yt-dlp fallback), transcodes to one format, writes full tags + cover art, BPM-analyzes, and files it by a path template. Ambiguous matches wait in an inbox instead of guessing.
>
> Bonus for hoarders: image/cover editing, lyrics fetch (plain + synced LRC), ISRC bulk-fill, and a duplicates page with recoverable trash.
>
> Docker, AGPLv3: https://github.com/PauloJf/BPM-Tagger

**Attach as gallery:**
`02-library.png` → `05-track-detail.png` → `07-settings.png`

**Rule:** Big-local-library crowd — the grabber + tagging pipeline is the hook. Keep it practical, not salesy.

---

## 5. r/running — cadence angle · post with care

**Read first:** Strictest of the set. Most tool/app posts get removed as
self-promotion. Safer routes: drop it in the **Daily Thread / Gear Tuesday** as a
comment, or use r/RunningApps. If you do post standalone, lead with the running
problem, not the software.

**Title (if standalone)**

> I self-host my music, so I built a cadence player that stretches my own songs to my target step rate (open source)

**Body**

> Cadence apps that come with their own music never had the songs I actually run to, and I already self-host my library. So I added a "Run mode" to a tool I maintain: I pick a target cadence (say 170), it queues songs from my own library whose BPM matches — it octave-folds, so a 85 BPM track works for a 170 cadence — and it time-stretches each one onto the exact step with pitch preserved, so nothing sounds chipmunky. Starred favourites come first.
>
> It's a web app that installs to your phone's home screen with lock-screen controls. Free and open source — sharing in case anyone else runs to their own library instead of a streaming playlist.
>
> Not selling anything; happy to talk cadence/BPM matching. [link]

**Attach (phone-first):**
`12-player-mobile.png` → `11-player-desktop.png`

---

## 6. r/Python — new audience · optional

Use the **Showcase** flair; the body follows the required What/Audience/Comparison
structure. Images go in a comment, not the post body.

**Title**

> BPM Tagger — three-detector BPM analysis for a music library, reconciled with octave-error correction (Flask + React, fully Dockerized)

**Body**

> **What My Project Does**
>
> BPM Tagger scans a music library and writes an accurate BPM tag to every file. Instead of trusting one detector, it runs three — a PyTorch CNN (deeprhythm), essentia's `RhythmExtractor2013`, and a multi-segment librosa analysis — then reconciles them: when two detectors disagree by a 2× factor (the classic octave error), the value inside your configured BPM range wins, and the result is normalized into `[BPM_MIN, BPM_MAX]`. librosa's beat-consistency doubles as a confidence score and tiebreaker. Genuine disagreements get flagged for review in a web UI.
>
> **Target Audience**
>
> Self-hosters running Navidrome/Subsonic who want reliable BPM tags — and, via a built-in cadence player, runners who want their own library matched to their step rate. It's a real deployment I use daily, not a toy.
>
> **Comparison**
>
> Most taggers (or a bare librosa call) use a single detector and silently ship octave errors. The three-detector reconciliation + explicit review queue is the difference. Tag writing covers ID3/Vorbis/MP4/plus ID3-in-WAV/AIFF/DSF via mutagen.
>
> **Stack:** Python 3.12, Flask + Waitress (threaded scan pipeline with pause/stop events), SQLite (WAL, additive migrations), React + Vite + TypeScript frontend served by Flask. Ruff + pytest in CI. AGPLv3.
>
> https://github.com/PauloJf/BPM-Tagger

**Drop in a comment:**
`05-track-detail.png` (detector bar) → `04-review.png` → `06-stats.png`

**Rule:** r/Python requires the Showcase flair and the What/Audience/Comparison structure (above). Images go in a comment, not the post body.

---

## 7. r/RunningApps — new audience · low-risk

**Title**

> Free, open-source cadence player that runs on your own music library and time-stretches songs to your exact step rate

**Body**

> Most cadence apps make you use their catalog. This one runs on music you already own. You set a target cadence (e.g. 168 spm), and it builds a queue from your own library of songs whose BPM matches — octave-folded, so an 84 BPM track works at native speed for a 168 cadence — favouring the ones you've starred.
>
> The part I like: a **tempo lock** that time-stretches each song onto your exact step with pitch preserved, so a song that's a couple BPM off still lands perfectly on your cadence without sounding sped-up. The queue auto-refills so a long run never goes silent.
>
> It's a self-hosted web app (installs to your home screen as a PWA, lock-screen controls, works with your Navidrome/Subsonic library). Free, open source, no account or subscription.
>
> https://github.com/PauloJf/BPM-Tagger

**Attach (phone-first):**
`12-player-mobile.png` → `11-player-desktop.png`

**Rule:** Small but tolerant of app shares. Note the self-hosted requirement up front so people know it's not a plug-and-play phone app.

---

## Not this round

- **r/homelab** — wants hardware/rack/infra content; a software self-promo reads as
  off-topic spam unless framed inside a "what I deployed this week" post.
- **r/DataHoarder** — overlaps with r/musichoarder but skews toward acquisition/storage
  at scale. Post there only if the grabber (bulk download + filing) is your lead, and
  expect a cooler reception than r/musichoarder.
