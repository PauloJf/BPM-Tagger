# Ecosystem decisions

Answers given by the owner on **2026-11-10**, before the unattended build. This file is the
authority for that build: anything not covered here takes the most conservative, reversible
option and is logged in `MORNING-REPORT.md` under "Decisions I made without you".

| # | Question | Answer | Date |
|---|---|---|---|
| D1 | ADR-001 topology | **Two repos + a thin private ecosystem layer living in Music Grabber** (`Music-Grabber/ecosystem/`: compose bundle, gateway config, contract tests). Public contracts and tokens live in BPM Tagger. No monorepo, no plugin. | 2026-11-10 |
| D2 | ADR-002 identity | **BPM Tagger is the identity provider.** Shared `ECOSYSTEM_SECRET`; 60 s HMAC-signed tokens; Music Grabber exchanges them for its own session. admin → admin, member → user, player → no access. Music Grabber's local login stays. Off unless the secret is set. | 2026-11-10 |
| D3 | ADR-003 Navidrome | **Optional.** Every Navidrome sync keeps working, opt-in. Stop describing it as required (README / CLAUDE.md positioning may change). Close the Subsonic gaps; Music Grabber's playlist mirror may target BPM Tagger. | 2026-11-10 |
| D4 | ADR-004 frontend | **Gateway, BPM Tagger's look wins.** Caddy: `/` → BPM Tagger, `/grabber/` → Music Grabber. Same navigation groups, deep links both ways. Music Grabber gets an ecosystem theme mapping its tokens onto BPM Tagger's colours; its standalone look stays. | 2026-11-10 |
| D5 | ADR-005 native apps | **No native app. Written spike only**, plus device-free PWA hardening where possible. | 2026-11-10 |
| D6 | Event flow | **Pull feed with a cursor** (`GET /api/v1/events?after=…`, long-poll) on both services, plus a "scan these paths now" hint to BPM Tagger. No webhooks, no broker. Existing polling stays as the fallback. | 2026-11-10 |
| D7 | Roadmap order, drops | **1, 2, 3, 4, 5, 6, 7, 8 as in `roadmap.md`.** Nothing dropped; slice 8 is the spike document only. | 2026-11-10 |
| D8 | Draft PRs | **Yes**: one draft PR per repo as the last step, never merged. | 2026-11-10 |
| D9 | Branch | `claude/music-ecosystem-consolidation-egd174` in both repos (the session's branch; the brief's `…-89lh5l` is not used). | 2026-11-10 |
| D10 | BPM evaluation set | **Synthetic audio with known tempo** (incl. half / double-time traps and tempo ramps) for the overnight numbers, **plus a harness mode that scores against reviewed / locked tracks in an existing `bpm_tagger.db`** for the owner to run on the real library. No dataset downloads. | 2026-11-10 |
| D11 | Accuracy changes | Ship **behind a toggle, default off**, with before / after numbers from the harness. | 2026-11-10 |
| D12 | SSO account linking | **Auto-link by username** (case-insensitive); otherwise create a new Music Grabber user. BPM Tagger's admin links to Music Grabber's first admin, overridable with `SSO_ADMIN_USERNAME`. | 2026-11-10 |
| D13 | Names, paths, ports | Caddy on host port **8080**; BPM Tagger at `/`, Music Grabber at `/grabber/`; env vars `ECOSYSTEM_SECRET` (both), `UI_BASE_PATH` and `SSO_ADMIN_USERNAME` (Music Grabber), `ECOSYSTEM_EVENTS`; bundle in `Music-Grabber/ecosystem/`, compose project `music`; Navidrome as an optional compose profile. Anything else follows each repo's conventions. | 2026-11-10 |
| D14 | Repos in scope | **Only BPM-Tagger and Music-Grabber.** No third repo, no new repo, no visibility change. | 2026-11-10 |
| D15 | Defaults for new behaviour | **Sensible defaults on** for low-risk improvements (Subsonic gap fixes, per-user album / artist stars, `HEALTHCHECK`s, empty-list stubs). **Opt-in, default off** for anything that writes tags, changes detection, or changes login. | 2026-11-10 |

## Standing rules (from the brief; not up for decision overnight)

- Plan usage only: no paid review agents, cloud services, billable tools or paid APIs. Any
  "ask first" cost prompt is answered "no" and logged as a follow-up.
- Never: `VERSION` bumps, tags, releases, merges, pushes to `main`, force-pushes, new
  repositories, visibility changes, dropping tables or user data, real network in tests, secrets
  in the repo or logs.
- BPM Tagger's public tree stays free of download mechanics, Spotify OAuth and provider code;
  Music Grabber detail goes in its own private docs.
- Core isolation (`tests/test_core_isolation.py`, `tests/test_isolation.py`) is not relaxed.
- Additive migrations only, with a rollback path; audio files are touched only in the tags a
  feature owns.
- Stuck for about an hour on one blocker: disable or revert, log under "Blocked", move on.
