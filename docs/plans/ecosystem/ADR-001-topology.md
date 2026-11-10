# ADR-001: Repo and service topology

Status: **accepted as recommended** (2026-11-10), see [decisions.md](decisions.md).

## Context

Two shipped projects: BPM Tagger (public, AGPL, image `gatoserio/bpm-tagger`) and Music Grabber
(private, AGPL, image `gatoserio/music-grabber`). The grabber was deliberately detached
([grabber-detach.md](../grabber-detach.md)): no Python imports, no shared DB, coupling over HTTP and
the music folder, enforced by `tests/test_core_isolation.py` here and `tests/test_isolation.py`
there. Acquisition code must never reach a public repo or image.

## Options

| | (a) Two repos + thin private ecosystem layer | (b) Private monorepo, BPM Tagger as a filtered public mirror | (c) Music Grabber as an optional private plugin of BPM Tagger |
|---|---|---|---|
| Public/private rule | Safe by construction: nothing private is ever in the public tree | One bad filter rule or a mis-pushed branch publishes private code; history rewriting on every sync | The plugin loads into the public image's process; public code grows plugin hooks shaped by acquisition |
| Isolation tests | Unchanged | Must be re-expressed as path rules inside one tree | Reversed: a plugin *is* a Python import |
| Release independence | Kept (VERSION bump vs GitHub release) | Lost or simulated with path filters; the public mirror's CI/publish trigger has to be rebuilt | Coupled: plugin ABI ties both versions |
| Effort | S: compose bundle, gateway, contracts | L: history merge, mirror tooling, CI rebuild, contributor story | M-L: plugin API, packaging, a second image variant |
| Dev ergonomics | Two checkouts; cross-repo changes need two PRs (mitigated by versioned contracts + loopback fakes) | Best for cross-cutting changes | One process to debug, but fast-breaking provider deps return to the BPM Tagger image |
| Reverses a recorded decision | No | Partly (one repo again) | Yes (grabber-detach, extraction.md) |

## Recommendation

**(a).** It is the only option that keeps every recorded decision, and the pain it leaves (two PRs
per cross-cutting change) is what versioned contracts are for.

The ecosystem layer is: a compose bundle with a single-origin gateway, the contract files and
their tests, and the cross-app navigation contract. Placement:

- **Public, in BPM Tagger:** everything BPM Tagger itself serves or consumes (its `/api/v1`
  contract, the identity token format, the event feed, design tokens). None of it names a
  download mechanism.
- **Private, in Music Grabber (`ecosystem/`):** the compose bundle that includes Music Grabber,
  the gateway config, and the contract tests for Music Grabber's side. If a dedicated private
  repo is created later, this folder moves as-is.

A generic, acquisition-free bundle (BPM Tagger + optional Navidrome + gateway) may also live in
BPM Tagger so public users get the single-origin setup.

## Consequences

- Cross-repo features ship as two changes; the older side must keep working against the newer
  one (additive contracts, capability discovery, never a flag day).
- Duplicated helpers stay duplicated and pinned (`text.py` golden file), not shared as a package.
