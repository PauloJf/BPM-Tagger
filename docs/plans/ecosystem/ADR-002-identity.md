# ADR-002: Identity

Status: **accepted as recommended** (2026-11-10), see [decisions.md](decisions.md).

## Context

Three account stores: BPM Tagger (row-less admin + `players` with `player` / `member` roles,
cookie session + CSRF, optional admin TOTP), Music Grabber (its own users and bearer sessions,
roles admin / user), and per-account Subsonic credentials (deliberately separate from the web
password, [subsonic-api.md](../subsonic-api.md)). Ratings, dislikes and plays are already keyed to
BPM Tagger accounts (`admin` / `player:<id>`); follows and hides are keyed to Music Grabber users.

## Options

| | (1) BPM Tagger is the identity provider | (2) Small shared auth service | (3) OIDC via an existing IdP (Authelia, Authentik, Keycloak) |
|---|---|---|---|
| New moving parts | None | A third service, DB and image to maintain | A fourth service the household must run and understand |
| Fits roles already there | Yes: admin / member / player exist with enforcement | Must re-model them | Roles become IdP groups, mapped twice |
| Music Grabber standalone | Still works: local login stays | Broken unless the service is bundled | Works only with an IdP |
| Migration | Music Grabber links its users to BPM Tagger accounts | Both sides migrate | Both sides migrate; passwords reset |
| Subsonic credentials | Stay where they are | Unclear owner | IdPs do not do Subsonic token auth |
| Effort | M | L | L, plus docs burden |

## Recommendation

**(1).** BPM Tagger is always present, public, and already owns roles and per-user data.

**Mechanism** (no shared DB, no Python imports):

- One shared secret, `ECOSYSTEM_SECRET` (>= 32 chars), set on both services. Unset = feature off,
  both apps behave exactly as today.
- BPM Tagger issues a short-lived (60 s) HMAC-SHA256 signed token to a signed-in browser:
  `GET /api/ecosystem/token` → `{sub, username, role, iat, exp, aud}` with `sub` =
  `admin` / `player:<id>`. Format is a documented, versioned contract (stdlib only on both sides).
- Music Grabber's UI, served on the same origin by the gateway (ADR-004), exchanges it at
  `POST /api/auth/sso` for one of its normal session tokens. Its local login form stays as the
  fallback and for standalone installs.
- Role mapping: admin → admin, member → user, player (kiosk) → **no access**. The role is re-read
  on every exchange, so a demotion or a disabled account takes effect at the next exchange;
  Music Grabber SSO sessions are short (1 h) for that reason.
- Server-to-server calls use the same token format with `sub = service:<name>`, replacing the
  admin-password login Music Grabber's analysis trigger uses today.
- Subsonic credentials stay per account in BPM Tagger, unchanged.

**Migration** (additive, reversible):

- Music Grabber: `users.external_sub` (new nullable column). On first SSO exchange the account is
  linked to the existing user with the same username (case-insensitive), else a new user is
  created. BPM Tagger's admin links to the Music Grabber admin named by `SSO_ADMIN_USERNAME`
  (default: its first admin). Follows, hides and dismissals stay on the linked row, so nothing
  moves.
- BPM Tagger: no schema change for identity. Per-user gaps (playlist owner, per-owner album /
  artist stars) are separate additive columns, backfilled to `admin`.
- Rollback: unset `ECOSYSTEM_SECRET`. Local logins never stopped working; `external_sub` is ignored.

## Consequences

- The secret is as sensitive as the admin password: env-only, never logged, never sent to a browser.
- BPM Tagger's row-less admin stays; turning it into a row is not needed for this and is not planned.
- Username-based linking trusts BPM Tagger's admin (the only one who creates accounts there).
