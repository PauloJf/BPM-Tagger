# ADR-004: Frontend

Status: **accepted as recommended** (2026-11-10), see [decisions.md](decisions.md).

## Context

Two React + Vite + Tailwind SPAs. BPM Tagger's is public and must not contain acquisition UI
(the detach removed exactly that). Music Grabber's assumes it is served at `/`. The two design
systems share no token names and look different (dark oklch vs warm "liner notes").

## Options

| | (1) Gateway: two SPAs, one origin, shared navigation + tokens | (2) One SPA with feature modules | (3) Module federation |
|---|---|---|---|
| Public/private rule | Safe: each SPA stays in its repo | Acquisition UI must live in, or be built into, the public app | Safe, but the public shell needs a remote-loading contract shaped by private code |
| Works with either app missing | Yes | Needs a private build variant | Yes, with runtime fallbacks |
| Effort | M: a base path for Music Grabber, nav contract, token alignment | L: re-merge what was just split | L: Vite federation plugins, shared-dependency version pinning |
| Feels like one app | Mostly: same origin, same nav, same look; a full page load when crossing | Fully | Fully |
| Solo-maintainer cost | Low | Medium | High (version skew between remotes) |

## Recommendation

**(1).**

- **Gateway:** Caddy in the compose bundle. `/` → BPM Tagger, `/grabber/*` → Music Grabber (prefix
  stripped). Music Grabber gains `UI_BASE_PATH` (default `/`, so standalone installs are unchanged).
- **Navigation contract:** each app asks its own backend which sibling is configured and renders
  the same groups: Play (Run, Listen) · Library (Browse, Playlists, Cadences) · Discover (For you,
  New releases, Search) · Downloads (Queue, Inbox, Spotify playlists) · System. Entries owned by
  the other app are plain links. BPM Tagger shows the Discover / Downloads groups only when a
  Music Grabber is connected, exactly as its request button is gated today.
- **Deep links** (a documented URL contract, additive on both sides):
  Related panel → `/grabber/artist/<id>`, `/grabber/album/<id>`; a finished download →
  `/track?path=…` in BPM Tagger; a new release → queue → library → Run.
- **Design system:** one canonical token set. Music Grabber keeps its own stylesheet and gets a
  theme that maps its tokens onto the canonical values, switched on when it runs inside the
  ecosystem. No shared npm package to publish: the token file is small and is pinned by a test,
  like `text.py`.

## Consequences

- Crossing between the apps is a page load; the player bar does not follow you into Music
  Grabber's pages. Audio keeps playing only if the page is opened in another tab. Accepted for
  now; revisit only if it hurts in daily use.
