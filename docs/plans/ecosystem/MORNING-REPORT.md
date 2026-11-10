# Morning report

Unattended build started 2026-11-10 after "go reordered". Branch
`claude/music-ecosystem-consolidation-egd174` in both repos. Authority: [decisions.md](decisions.md).

## TL;DR

- **Single-origin bundle** (slice 1): in a Music Grabber checkout, `cd ecosystem && cp .env.example .env`
  (fill in `MUSIC_PATH`, `BPM_UI_PASSWORD`, `BPM_UI_SECRET_KEY`, `MG_API_TOKEN`), then
  `docker compose up -d --build`. BPM Tagger: <http://127.0.0.1:8080/>, Music Grabber:
  <http://127.0.0.1:8080/grabber/>. **Not run under Docker here** (no daemon in the build container).

## Slices

| # | Slice | Status | Commits | Repo |
|---|---|---|---|---|
| 1 | Skeleton | **done** (Docker run not verified) | BT `99b35fd`, MG `4e9eaa9` | both |
| 2 | Identity | in progress | | both |
| 3 | Event flow | not started | | both |
| 6 | Subsonic completeness + Navidrome optional | not started | | BPM Tagger |
| 7 | Analysis quality | not started | | BPM Tagger |
| 4 | UI shell | not started | | both |
| 5 | Discovery | not started | | both |
| 8 | Native spike | not started | | docs |

## How to run and verify

### Slice 1: the bundle

1. `cd Music-Grabber/ecosystem && cp .env.example .env`, fill in the four required values.
2. `docker compose up -d --build` (add `--profile navidrome` for Navidrome). `docker compose ps`
   should show three healthy containers.
3. Open `/` (BPM Tagger) and `/grabber/` (Music Grabber: first-run setup or login). Reload a deep
   page such as `/grabber/queue`; it must come back, not 404.
4. Existing installs: stop them, copy their data folders to `ecosystem/data/bpm-tagger/` and
   `ecosystem/data/music-grabber/`, start the bundle. Rollback: stop the bundle, start the old
   compose files; nothing was migrated. Without `UI_BASE_PATH` Music Grabber serves at `/` as before.

(later slices are added as they land)

## Gate results

| Repo | ruff | pytest | typecheck | frontend tests | build |
|---|---|---|---|---|---|
| BPM Tagger (baseline) | clean | 1053 passed | not run yet | not run yet | not run yet |
| Music Grabber (baseline) | clean | 750 passed | | | |
| BPM Tagger after slice 1 | clean | 1059 passed | not run (frontend untouched) | not run | not run |
| Music Grabber after slice 1 | clean | 777 passed | clean | 81 passed (12 files) | built |

## Decisions I made without you

| Decision | Alternative | How to switch |
|---|---|---|
| The gateway publishes on **loopback** by default (`127.0.0.1:8080`) | Publish on all interfaces as "host port 8080" could be read | `GATEWAY_BIND=0.0.0.0` in `ecosystem/.env` |
| The bundle uses `LIBRARY_INDEX=filesystem` for Music Grabber | `opensubsonic` against BPM Tagger (needs an API key created by hand first) | change the variable in `ecosystem/docker-compose.yml` |
| Health checks **pass** when the UI / API is off, instead of reporting unhealthy | Fail when nothing listens | edit the `HEALTHCHECK` line in each Dockerfile |
| `UI_BASE_PATH` is handled by rewriting the served page and manifest, with the gateway stripping the prefix; Music Grabber's routes stay at the root | Mount every route below the prefix | n/a (unset = unchanged) |
| The requests contract is one JSON file copied into both repos and pinned by digest in both test suites | A shared package or submodule | n/a |

## Blocked / not done / not verified

- **`docker compose up` was never run**: the build container has no Docker daemon. The compose
  file, Caddyfile and env example are covered by static tests only.
- **The Caddyfile was never loaded by a real Caddy**: downloading the binary was refused by the
  session's permission rules. What *was* verified in a real browser (Chromium): Music Grabber
  running with `UI_BASE_PATH=/grabber` behind a stand-in proxy that strips the prefix; a deep link
  (`/grabber/queue`) loads, assets and `/grabber/api/...` calls return 200, no page errors.

## Needs you

- Run the bundle once under Docker and confirm the three containers turn healthy and both UIs load.

## Suggested next session

(filled in at the end)
