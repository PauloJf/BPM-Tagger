# Ecosystem roadmap

Status: **accepted** (2026-11-10); order confirmed in [decisions.md](decisions.md).
Every slice is additive, off unless configured, and leaves both repos shippable. BT = BPM Tagger,
MG = Music Grabber. Sizes: S < half a day, M ~ a day, L several days of focused work.

| # | Slice | Size | Repo | Depends on | User-visible result | Migration / rollback |
|---|---|---|---|---|---|---|
| 1 | **Skeleton**: compose bundle with a Caddy gateway (single origin), `HEALTHCHECK`s, MG `UI_BASE_PATH`, contract folder + loopback contract-test harness, schema for MG's existing `/api/v1` | M | both | none | One `docker compose up`, one URL: `/` and `/grabber/` | None. Remove the bundle; both apps unchanged at `/` |
| 2 | **Identity**: `ECOSYSTEM_SECRET`, BT token endpoint, MG SSO exchange, account linking, service tokens | L | both | 1 | Sign in once; MG knows who you are and your role | MG `users.external_sub` (additive). Rollback: unset the secret |
| 3 | **Event flow**: `events` table + feed on both sides, intake hint, consumers replace the polling loops | M | both | 2 (service token) | A download is playable within seconds of landing; request status updates live | Additive tables. Rollback: unset; polling paths remain as fallback |
| 4 | **UI shell**: shared navigation groups, deep links both ways, canonical tokens + MG ecosystem theme | M | both | 1, 2 | One menu across Library / Run / Listen / Discover / Downloads / Playlists / Settings | None (UI only). Hidden when no sibling is connected |
| 5 | **Discovery**: per-user taste (ratings, dislikes, plays) served by BT to MG; "download album" from BT's Related panel; weekly ntfy digest | M | both | 2, 3 | For you reflects *your* ratings, not the library's; digest of new releases | Additive cache columns. Rollback: toggle off → library-wide taste as today |
| 6 | **Subsonic completeness + Navidrome optional**: gaps from ADR-003, client compatibility table, mirror can target BT, docs repositioning | M | BT (+ MG mirror target) | none | Third-party clients work per user without Navidrome | `subsonic_stars.owner` (additive, backfill `admin`). Rollback: old rows untouched |
| 7 | **Analysis quality**: BPM evaluation harness; octave / variable-tempo improvements shipped only with before / after numbers; ReplayGain / R128 tag writing (opt-in); true synced lyrics frames; per-track and bulk re-run in the activity feed | L | BT | none | Fewer half / double-time errors; volume levelling in every client; synced lyrics in clients that read tags | New tags only, behind toggles, mtime policy as today. Rollback: toggles off; tags are standard and harmless |
| 8 | **Native**: per ADR-005 (default: written spike only) | S | BT docs | 6 | A decision document with real-device test steps | none |

## Independence

- 6, 7 and 8 are independent of everything else and of each other, and are BT-only (fully
  verifiable without a second service).
- 1 → 2 → 3 is a chain; 4 needs 1 and 2; 5 needs 2 and 3.
- Stopping after any slice is coherent: each one is either complete or switched off.

## Explicitly not planned

Merging the repos or databases; moving acquisition UI into BT; removing Navidrome sync; turning
BT's admin into a table row; a shared Python package; webhooks; a native codebase (unless ADR-005
says otherwise).
