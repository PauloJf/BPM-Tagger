# Morning report

**Paused by the owner after slice 2 (2026-11-10); work continues in stages.** Build started
2026-11-10 after "go reordered". Branch
`claude/music-ecosystem-consolidation-egd174` in both repos. Authority: [decisions.md](decisions.md).

## TL;DR

- **Single-origin bundle** (slice 1): in a Music Grabber checkout, `cd ecosystem && cp .env.example .env`
  (fill in `MUSIC_PATH`, `BPM_UI_PASSWORD`, `BPM_UI_SECRET_KEY`, `MG_API_TOKEN`), then
  `docker compose up -d --build`. BPM Tagger: <http://127.0.0.1:8080/>, Music Grabber:
  <http://127.0.0.1:8080/grabber/>. **Not run under Docker here** (no daemon in the build container).
- **Single sign-on** (slice 2): add one `ECOSYSTEM_SECRET=<32+ characters>` line to `ecosystem/.env`,
  `docker compose up -d`, sign in to BPM Tagger, open `/grabber/`: you are signed in. Admin → admin,
  member → user, kiosk players have no access. Off unless the secret is set.
- Stopped here at the owner's request. Next: slice 3 (see "Suggested next session").

## Slices

| # | Slice | Status | Commits | Repo |
|---|---|---|---|---|
| 1 | Skeleton | **done** (Docker run not verified) | BT `99b35fd`, MG `4e9eaa9` | both |
| 2 | Identity | **done** (Docker run not verified) | BT `2901c12`, MG `044d414` | both |
| 3 | Event flow | not started (**resume here**) | | both |
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

### Slice 2: single sign-on

1. Set the same `ECOSYSTEM_SECRET` (32+ characters, e.g. `python -c "import secrets; print(secrets.token_urlsafe(48))"`)
   on BPM Tagger and Music Grabber. In the bundle: one line in `ecosystem/.env`.
2. Sign in to BPM Tagger as the admin or a member, then open
   `/api/ecosystem/token?aud=music-grabber`: you should get `{"token": "v1...", "expires_in": 60}`.
   A kiosk player gets 403; without the secret the route is 404.
3. Open `/grabber/`: Music Grabber shows the app, signed in, with no login form. The session
   lasts one hour, then the page signs on again by itself on the next load.
4. Migration: Music Grabber adds three nullable columns on first start (`users.external_sub`,
   `users.sso_managed`, `sessions.sso`) after writing its usual `.bak-<version>` copy of the DB.
   BPM Tagger: no schema change.
5. Rollback: unset `ECOSYSTEM_SECRET`; local logins never stopped working and the new columns
   are ignored. An older Music Grabber image runs fine on the migrated DB (columns are additive).

## Gate results

| Repo | ruff | pytest | typecheck | frontend tests | build |
|---|---|---|---|---|---|
| BPM Tagger (baseline) | clean | 1053 passed | not run yet | not run yet | not run yet |
| Music Grabber (baseline) | clean | 750 passed | | | |
| BPM Tagger after slice 1 | clean | 1059 passed | not run (frontend untouched) | not run | not run |
| Music Grabber after slice 1 | clean | 777 passed | clean | 81 passed (12 files) | built |
| Music Grabber after slice 2 | clean | 818 passed | clean | 86 passed (12 files) | built |
| BPM Tagger after slice 2 | clean | 1071 passed (core-only subset: 100) | clean (baseline, frontend untouched) | 402 passed (40 files, baseline) | built (baseline) |

## Decisions I made without you

| Decision | Alternative | How to switch |
|---|---|---|
| The gateway publishes on **loopback** by default (`127.0.0.1:8080`) | Publish on all interfaces as "host port 8080" could be read | `GATEWAY_BIND=0.0.0.0` in `ecosystem/.env` |
| The bundle uses `LIBRARY_INDEX=filesystem` for Music Grabber | `opensubsonic` against BPM Tagger (needs an API key created by hand first) | change the variable in `ecosystem/docker-compose.yml` |
| Health checks **pass** when the UI / API is off, instead of reporting unhealthy | Fail when nothing listens | edit the `HEALTHCHECK` line in each Dockerfile |
| `UI_BASE_PATH` is handled by rewriting the served page and manifest, with the gateway stripping the prefix; Music Grabber's routes stay at the root | Mount every route below the prefix | n/a (unset = unchanged) |
| `ECOSYSTEM_SECRET` is **env only** in BPM Tagger (ignored if present in `settings.json`, not editable in Settings) | Editable in Settings like the Music Grabber key | remove it from `ENV_ONLY_SETTINGS` in `bpm_tagger/config.py` |
| A member is **never** linked by username into a local Music Grabber **administrator**; a new user is created instead | Link by username regardless of role (the literal reading of D12) | remove the role check in `Ops.sso_login` |
| Linked pre-existing users keep their stored Music Grabber role; only accounts sign-on created follow BPM Tagger's role | Always overwrite the role from the token | `Ops.sso_login` |
| Sign-on sessions last 1 hour and do not slide | Normal 14-day sliding sessions | `SSO_SESSION_HOURS` in `music_grabber/ops.py` |
| The token module is one stdlib file copied byte-identical into both apps and pinned by hash | A shared package | n/a |
| The requests contract is one JSON file copied into both repos and pinned by digest in both test suites | A shared package or submodule | n/a |

## Blocked / not done / not verified

- **`docker compose up` was never run**: the build container has no Docker daemon. The compose
  file, Caddyfile and env example are covered by static tests only.
- **The Caddyfile was never loaded by a real Caddy**: downloading the binary was refused by the
  session's permission rules. What *was* verified in a real browser (Chromium): Music Grabber
  running with `UI_BASE_PATH=/grabber` behind a stand-in proxy that strips the prefix; a deep link
  (`/grabber/queue`) loads, assets and `/grabber/api/...` calls return 200, no page errors.
- **Sign-on was verified in a real browser, but not under Docker**: both apps running as plain
  processes with the same secret behind the stand-in proxy. Signed out of BPM Tagger, Music Grabber
  shows its own form; after signing in to BPM Tagger, `/grabber/` fetches the token, exchanges it
  and shows the app as `admin`. The secret appears in neither app's log. Not exercised by hand: a
  member account, the one-hour expiry, and TOTP-enabled admins (all covered by automated tests only).
- **No independent security review** of the sign-on code (paid review tools were excluded).
- Music Grabber: an SSO-created account's role sync has no "last admin" guard (only BPM Tagger's
  admin maps to admin, and `API_TOKEN` always works, so nobody can be locked out).

## Needs you

- Review the sign-on code before merging (`bpm_tagger/web/ecosystem_token.py`, `web/api/ecosystem.py`,
  and Music Grabber's exchange): it is a new way into both apps and no paid review was run.
- Run the bundle once under Docker and confirm the three containers turn healthy and both UIs load.

## Suggested next session

> Continue the ecosystem build from `BPM-Tagger/docs/plans/ecosystem/` (`decisions.md` is the
> authority, `roadmap.md` the plan, this report the state). Slices 1 and 2 are done on
> `claude/music-ecosystem-consolidation-egd174` in both repos. Build order for the rest:
> **3 (event flow), 6 (Subsonic completeness), 7 (analysis quality), 4 (UI shell), 5 (discovery),
> 8 (native spike doc)**. Same rules as before: one slice at a time, gates green, commit and push
> per slice, update this report, stop after each slice and tell me. First re-run the baseline
> gates, and check the "not verified" list above (the Docker run of the bundle).
