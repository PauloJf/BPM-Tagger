# Detaching the music grabber

The optional grabber (Spotify playlist watching + downloading) moved out of this
repo into its own project, **Music Grabber**. This page is the BPM Tagger side of
that: what leaves, in what order, and what must be true before it ships.

Why: it carried the download providers (legal/ToS exposure for a public repo and
image), heavy fast-breaking dependencies, and a different release cadence. Its
coupling to the core was thin: the library tag index, the `managed` flag, and
post-download BPM analysis, which now happens by BPM Tagger picking new files up
from the music folder in watch mode.

## Steps (each its own PR, stacked)

| # | Step | Status |
|---|---|---|
| 1 | **Neutral modules first.** `grabber/matching|tagging|path_template` → `bpm_tagger/matching|tagging|path_template`, importers repointed, layering test extended. No behaviour change. | ✅ Done (branch `refactor/neutral-modules`, PR #16) |
| 2 | **Remove the wiring.** `main.py`, `scanner.grabber`, restart hook, `PeriodicSync` Spotify branch, grabber blueprints (`spotify`, `queue`, `inbox`, the Suggestions half of `suggestions`), settings endpoints, `send_grabber`, config keys; delete `bpm_tagger/grabber/`; Related endpoints → `web/api/related.py`; ISRC / metadata / image lookups drop Spotify and the Deezer ARL. | ✅ Done (branch `refactor/remove-grabber-backend`) |
| 3 | **Frontend.** Delete the grabber-only pages and components (Inbox, Queue, Suggestions, Add Music, GrabberGate, the playlist suggestions panel), edit the mixed ones (Playlists, Settings, Stats, Related), remove nav entries. | ✅ Done (same branch) |
| 4 | **Schema.** Stop creating `grab_*`, `oauth_tokens` and `suggestions` for *new* databases. **Never drop them from existing ones** (rollback safety). | ✅ Done (branch `refactor/legacy-tables`) |
| 5 | **Dependencies and image.** `yt-dlp` and `streamrip` out of `requirements.txt` (done in step 2); keep `rapidfuzz` and `pillow`. Docs: README, DOCKERHUB_README, CLAUDE.md, compose, `.env.example`. | ✅ Done (same branch) |
| 6 | **Beta, then release.** Test the removal on the beta channel (`vX.Y.Z-beta.N`), then ship as a breaking release. | 🔶 Betas 1–3 published and tested; release PR `release/v2.19.0` prepared (VERSION 2.19.0, notice in README / Docker Hub README / CHANGELOG), **not merged** — merging it publishes `:latest` |

## Release rule (hard)

A pushed `VERSION` bump on `main` **is** the Docker Hub release trigger. The removal
must not ship that way until Music Grabber is stable: **≥ 1.0.0**, its import tool
verified on at least one real migrated database, and the intake flow (it writes into
the music folder, BPM Tagger tags the files) verified end to end against a running
BPM Tagger. Until then the work lives on branches and can ship as a beta only
(`docker-beta.yml` never touches `:latest`).

The removal is breaking for grabber users, so it gets a minor or major bump and a
prominent CHANGELOG entry. Upgrading must stay safe: stale settings keys are ignored
(and left in `settings.json`), Spotify playlists stay as static playlists, and no
table is dropped.

## What users keep, and what they lose

Keep: the BPM engine, Run, Listen, Navidrome and Local playlists (diff / merge /
split), the Related panel with previews and the artist catalog browser, lyrics,
artwork, ratings, the Subsonic API.

Lose: Spotify as a playlist source (old playlists become static), the Add Music,
Suggestions, Queue and Inbox pages, Download missing, and Spotify as a cover /
metadata / ISRC source.
