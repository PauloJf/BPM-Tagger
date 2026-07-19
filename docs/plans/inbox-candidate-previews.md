# Plan: Inbox candidate previews (listen before you choose)

> Current status of all plans is tracked in [STATUS.md](STATUS.md).

Status: **✅ done 2026-07-19 — Part A + Part B shipped (Unreleased).**
Written to be built by a coding agent without further context — every touched file,
signature, and edge case is spelled out. Read "What already exists" first: most of the
machinery shipped with the Suggestions page (v2.5.0) and must be reused, not rebuilt.

## Goal

On `/inbox`, each candidate card gets a ▶ **30-second preview** so you can *hear* the
candidate before clicking **Choose** — the audio equivalent of what the Duplicates →
Compare flow does for local files. Optionally (Part B) the **source track** (the Spotify
track the grabber is trying to fulfill) also gets a preview via a Deezer ISRC lookup, so
you can A/B the source against a candidate.

Deezer candidates get real clips. **yt-dlp candidates have no 30 s clip** — their `url`
is a YouTube page; they get an "open source page" external link instead. Since Deezer is
the primary provider, this covers most inbox decisions.

## What already exists (reuse, do not rebuild)

| Piece | Where | Notes |
|---|---|---|
| Ducking preview player | [player.tsx](../../frontend/src/lib/player.tsx) — `preview(track)`, `endPreview()`, `previewing` | Plays a one-off clip, fades the queue down, auto-resumes on end. `PlayerTrack.src` (absolute URL instead of `audioUrl(path)`) and `ephemeral` (never persisted) already exist. |
| `PreviewButton` | [trackBits.tsx:90](../../frontend/src/components/trackBits.tsx) | ▶/⏸ toggle for any row carrying a `preview_url`. Handles the isCurrent / ducking / toggle logic. **This plan extends it with lazy URL resolution** (see Frontend §2). |
| Deezer catalog client | [deezer_catalog.py](../../bpm_tagger/integrations/deezer_catalog.py) | `_get()` is rate-limited (`deezer_limiter.acquire()`), short timeouts, log-and-return-empty on failure. `track_isrc()` at line ~214 is the pattern to copy — it already hits `GET track/{id}`, whose response also carries `preview`. |
| CSP | [app.py:220](../../bpm_tagger/web/app.py) | `media-src 'self' https://*.dzcdn.net;` — **no CSP change needed.** |
| Candidate storage | `grab_candidates` table ([db.py:284](../../bpm_tagger/db.py)) | Has `provider`, `provider_track_id`, `url`, `cover_url` — **no `preview_url` column, and we are NOT adding one** (see Design decisions). `db.get_grab_candidate(id)` exists. |
| Inbox API | [inbox.py](../../bpm_tagger/web/api/inbox.py) — `inbox_bp` | `login_required` everywhere; `_check_csrf()` on POSTs only. New endpoints go here. |
| Inbox page | [Inbox.tsx](../../frontend/src/pages/Inbox.tsx) | `CandidateCard` renders the chip row (provider / quality / score / Δ) — the ▶ goes there. |
| Types | [types.ts:216](../../frontend/src/lib/types.ts) `GrabCandidate` | Already carries `id`, `provider`, `provider_track_id`, `url`. **No type changes needed.** |

## Design decisions (read before coding)

1. **Lazy resolution, no schema change.** Deezer preview URLs (`cdns-preview-*.dzcdn.net`)
   are not immortal — storing them at search time risks stale links by the time a human
   opens the inbox. And resolving at insert time would add up to ~N extra Deezer calls per
   searched item inside the `GrabWorker` threads for candidates nobody may ever listen to.
   Instead: a small GET endpoint resolves the URL **on first click**, with an in-memory
   TTL cache.
2. **Deezer-only clips.** `provider == "deezer"` → ▶ preview. `provider == "ytdlp"` (or
   anything else with a non-empty `url`) → a plain external link. No attempt to extract
   audio from YouTube for previewing (that would be a download, not a preview).
3. **Everything plays through the normal player.** No second `<audio>` element. Reuse
   `player.preview()` + `ephemeral: true` exactly like the Suggestions page — ducking,
   auto-resume, PlayerBar visibility, and "never persist across reload" all come free.
4. **Part B (source preview) is optional but cheap.** Deezer supports
   `GET https://api.deezer.com/track/isrc:{ISRC}` — most Spotify-sourced inbox items carry
   an ISRC, so the *intended* track can be previewed too. Ship Part A first; Part B is an
   independent commit.

---

## Part A — candidate previews ✅

### A1. `integrations/deezer_catalog.py` — one new function

Copy the `track_isrc()` pattern (module-level, `_get`, log-and-return-empty):

```python
def track_preview_url(dz_track_id: str) -> str:
    """The 30-second preview MP3 URL for a Deezer track id, "" on failure
    or when Deezer has no preview for the track."""
    if not dz_track_id:
        return ""
    try:
        d = _get(f"track/{dz_track_id}")
    except Exception as exc:
        log.debug("Deezer preview lookup failed for %s: %s", dz_track_id, exc)
        return ""
    return d.get("preview") or ""
```

(Yes, this is the same endpoint `track_isrc()` hits. Do **not** merge them into one
call-both-things function — call sites are different and both are cheap + cached.)

### A2. `web/api/inbox.py` — new endpoint + tiny cache

```python
GET /api/inbox/candidates/<int:cand_id>/preview
```

- `@login_required`, **no CSRF** (it's a GET). Lives in `inbox_bp`. Like the rest of the
  inbox API it needs only `state().db` — no grabber-enabled check (the page itself is
  behind `GrabberGate`).
- Look up via `db.get_grab_candidate(cand_id)` → unknown id → `404 {error: "not_found"}`.
- `provider != "deezer"` or empty `provider_track_id` → `200 {preview_url: "", dz_track_id: ""}`
  (uniform 200-with-empty, matching the "quiet failure" convention of `/api/related/*`).
- Otherwise resolve through a **module-level TTL cache** so repeated clicks / re-renders
  don't re-hit Deezer:

```python
_PREVIEW_TTL = 3600.0          # seconds; Deezer preview URLs live comfortably longer
_PREVIEW_CACHE_MAX = 500
_preview_cache: dict[int, tuple[float, str]] = {}   # cand_id -> (expires_at, url)
```

  Check cache → miss → `deezer_catalog.track_preview_url(cand["provider_track_id"])` →
  store (even when `""` — a track with no preview shouldn't be re-queried on every click).
  When the dict exceeds `_PREVIEW_CACHE_MAX`, evict the oldest-expiring entries. Same
  micro-cache style as the related-artists cache in
  [suggestions.py](../../bpm_tagger/web/api/suggestions.py) — look at it and match it.
- Response: `200 {preview_url: str, dz_track_id: str}` where `dz_track_id` echoes
  `provider_track_id` (the frontend uses it as the player identity key).
- Import: `from ...integrations import deezer_catalog` — match however
  `web/api/suggestions.py` imports it.

### A3. Frontend — generalize `PreviewButton` with lazy resolution

**[trackBits.tsx](../../frontend/src/components/trackBits.tsx)** — extend the existing
component; do **not** fork a second button with duplicated ducking logic:

```ts
export function PreviewButton({ track, resolveUrl }: {
  track: { dz_track_id: string; title: string; artist?: string; preview_url?: string };
  resolveUrl?: () => Promise<string>;   // lazy source, used when preview_url is absent
}) {
```

Behavior changes (existing call sites — Suggestions, RelatedPanel, QueueSimilar,
ArtistModal — pass `preview_url` directly and must keep working unchanged):

- Component state: `const [lazyUrl, setLazyUrl] = useState<string | null>(null)` and
  `const [loading, setLoading] = useState(false)`. Effective URL =
  `track.preview_url || lazyUrl`.
- On click with no effective URL and a `resolveUrl` prop: `setLoading(true)` → await
  `resolveUrl()` → on non-empty result `setLazyUrl(url)` **and immediately**
  `player.preview({...pt, src: url})` (first click must resolve *and* play — no
  click-twice); on empty result or throw, `setLazyUrl("")`.
- `lazyUrl === ""` (resolved, nothing available) → render the ▶ dimmed and `disabled`,
  `title="No preview available"`. `loading` → `disabled` with a subtle opacity change.
- All subsequent toggling (isCurrent / ducking / endPreview / toggle) stays exactly as
  it is today, operating on the effective URL.

**[Inbox.tsx](../../frontend/src/pages/Inbox.tsx)** — in `CandidateCard`'s chip row
(before the "Details" button, after the flex spacer):

```tsx
{cand.provider === "deezer" && cand.provider_track_id ? (
  <PreviewButton
    track={{ dz_track_id: cand.provider_track_id, title: cand.title || "", artist: cand.artist || "" }}
    resolveUrl={() =>
      api.get<{ preview_url: string }>(`/api/inbox/candidates/${cand.id}/preview`)
         .then((r) => r.preview_url)}
  />
) : cand.url ? (
  <a className="btn btn-bare btn-sm" href={cand.url} target="_blank" rel="noreferrer"
     title="Open source page" style={{ padding: "2px 6px", color: "var(--muted)" }}>
    {/* small external-link SVG, 13×13, stroke style matching the existing ic set */}
  </a>
) : null}
```

The synthetic player path is `preview:dz:${provider_track_id}` (built inside
`PreviewButton`) — consistent with Suggestions, so if the same Deezer track is previewed
from two pages the player treats it as the same clip.

No react-query needed for the resolution (it's a one-shot imperative fetch inside the
button); no invalidation implications.

---

## Part B — source-track preview (optional, separate commit) ✅

Lets you hear the *Spotify source* the candidates are being judged against.

### B1. `integrations/deezer_catalog.py`

```python
def track_by_isrc(isrc: str) -> dict:
    """Resolve an ISRC to {"dz_track_id": str, "preview_url": str}; {} on failure."""
    if not isrc:
        return {}
    try:
        d = _get(f"track/isrc:{isrc.strip().upper()}")
    except Exception as exc:
        log.debug("Deezer ISRC lookup failed for %s: %s", isrc, exc)
        return {}
    if not d.get("id"):
        return {}
    return {"dz_track_id": str(d["id"]), "preview_url": d.get("preview") or ""}
```

### B2. `web/api/inbox.py`

```python
GET /api/inbox/<int:item_id>/source-preview
```

- `@login_required`, GET, no CSRF. `db.get_grab_item(item_id)` → 404 if unknown. Do
  **not** require `awaiting_user` status (harmless on any item).
- No ISRC on the item → `200 {preview_url: "", dz_track_id: ""}`; else `track_by_isrc()`,
  cached in a second module-level TTL dict keyed by ISRC (same TTL/cap as A2).

### B3. Frontend

In `InboxCard` (Inbox.tsx), next to the item title/artist line, when `item.isrc` is
non-empty:

```tsx
<PreviewButton
  track={{ dz_track_id: `src:${item.id}`, title: item.title || "", artist: item.artist || "" }}
  resolveUrl={() =>
    api.get<{ preview_url: string }>(`/api/inbox/${item.id}/source-preview`)
       .then((r) => r.preview_url)}
/>
```

`dz_track_id` is only an identity key for the player — the synthetic `src:${item.id}`
form keeps it stable and distinct from candidate clips. (If you prefer the real Deezer
id, you'd need it before the first click; not worth plumbing.)

---

## Tests — `tests/test_inbox_preview.py`

Follow the conventions in [test_api_grabber.py](../../tests/test_api_grabber.py) for the
authenticated Flask test client + temp DB (see `tests/conftest.py`). All Deezer calls are
monkeypatched — **no network in tests**. Reset the module caches between tests
(`monkeypatch.setattr` the cache dicts to `{}` or clear them in a fixture).

Part A:
- Deezer candidate → endpoint returns the mocked `preview_url` + echoes
  `provider_track_id`.
- ytdlp candidate → `200 {preview_url: ""}` and the mocked catalog function was **not**
  called.
- Unknown candidate id → 404.
- Cache: two calls for the same candidate → catalog function invoked once (use a counter).
- Catalog failure (mock returns `""`) → `200 {preview_url: ""}`, no raise; the empty
  result is cached too (counter still 1 after a second call).
- Unauthenticated request → 401/redirect per the existing auth behavior in other API
  tests.

Part B:
- Item with ISRC → resolved id + url; item without ISRC → empty 200, catalog not called;
  unknown item → 404; ISRC cache hit → single catalog call.

Frontend gates: `cd frontend && npm run typecheck && npm run build && npm test` (the
vitest suite arrived in v2.6.11 — older "no test runner" claims are outdated).

Manual verification (dev: `run.ps1` + `npm run dev`, need `GRABBER_ENABLED=true` and an
item in `awaiting_user`):
1. ▶ on a Deezer candidate while a library track plays → queue ducks, clip plays, queue
   auto-resumes after ~30 s.
2. ▶ with nothing playing → clip plays standalone; reload the page → clip is gone from
   the player (ephemeral, not persisted).
3. Candidate with no Deezer preview → button dims to "No preview available" after the
   first click, no error flash.
4. ytdlp candidate → external-link opens the YouTube page in a new tab.
5. Existing Suggestions-page previews still work (regression check on the `PreviewButton`
   signature change).

Gates before commit: `pytest -q`, `ruff check bpm_tagger/ tests/`,
`cd frontend && npm run typecheck && npm run build`.

## Docs (required by project rules — same commit or immediate follow-up)

- **README.md**: one paragraph + screenshot under the grabber/inbox docs — "preview
  candidates before choosing (30 s Deezer clips; yt-dlp candidates link to their source
  page)".
- **DOCKERHUB_README.md**: mirror the feature bullet.
- **CHANGELOG.md**: new version entry (check `VERSION` and bump per project convention).

## Acceptance criteria

- ▶ on every Deezer candidate in the inbox; first click resolves **and** plays; repeated
  clicks toggle without re-hitting Deezer (server cache + component state).
- yt-dlp candidates show an external link, never a broken ▶.
- Clips run through the normal player: ducking + auto-resume when music is playing,
  PlayerBar shows the clip, nothing persists across reload.
- A candidate with no available preview degrades to a disabled button — no error flash,
  no console spam.
- No schema changes; no new env vars; no CSP changes; all Deezer traffic goes through
  `deezer_limiter`.
- Existing `PreviewButton` call sites (Suggestions, RelatedPanel, QueueSimilar,
  ArtistModal) behave exactly as before.
- (Part B) Source track previews when the item has an ISRC; silently absent otherwise.

## Out of scope

- Audio previews for yt-dlp candidates (would require downloading).
- Persisting preview URLs in `grab_candidates`.
- Waveform/segment comparison of candidate vs source (the Duplicates → Compare analogue);
  a possible follow-up once previews exist.
- Preview on Queue-page rows (pending/failed items) — inbox only for v1.
