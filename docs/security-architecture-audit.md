# BPM Tagger — Security & Architecture Audit

**Version audited:** v2.6.9 · **Commit:** `cc09f67` · **Date:** 2026-07-17
**Reviewer role:** Principal Engineer / Security Architect (production due-diligence)
**Scope:** Full application — backend (`bpm_tagger/`), frontend SPA (`frontend/`), database, API, PWA, dependencies, tests, CI.

> Verdict up front: this is a **well-built hobby/self-hosted app** with genuinely thoughtful security hygiene (CSRF, default-deny role scoping, path-traversal guards, SSRF awareness, secret masking). It is **not yet a hardened multi-tenant product**, and several real defects would bite in the next six months. Full scores and a go/no-go are at the end.

---

## How to read this

Each finding has: **Severity** (Critical / High / Medium / Low), **why it matters**, **file(s)**, the **code**, a **fix**, and **when** (immediately / before launch / after launch / if time permits). "Launch" here means "expose to more than one trusted user, or to the public internet."

A note on threat model: this app is designed to sit behind a single admin password (plus an optional low-privilege "player" password) on a home LAN or behind a reverse proxy. Many findings are Low *at that threat model* but rise to High/Critical the moment the instance is internet-facing. I've scored for the stated production intent (README markets it as publicly deployable, Docker-published, with `UI_PUBLIC_URL` + HTTPS support), so I treat "reachable by an attacker" as in-scope.

---

# 1. Security

## SEC-1 — SSRF: server-side image fetch follows redirects past the public-host guard — **HIGH** — fix *before launch*

**Why it matters:** The image-picker "apply from URL" and artist-image lookup fetch attacker-supplied URLs server-side. There is an SSRF guard (`_is_public_host`) — but it validates only the *original* URL's DNS, then calls `requests.get(...)` with the default `allow_redirects=True`. A public host that returns `302 → http://169.254.169.254/latest/meta-data/` (cloud metadata) or `→ http://192.168.1.1/` (LAN router/admin panel) sails straight through. It's also vulnerable to DNS-rebinding (TOCTOU between the `getaddrinfo` check and the request's own resolution).

**Files:** `bpm_tagger/web/api/images.py:55-72` (`_fetch_image`), `bpm_tagger/web/api/tracks.py:788` (artist image download).

```python
# images.py
def _fetch_image(url: str) -> bytes | None:
    if not _is_public_host(url):           # checks ORIGINAL url only
        return None
    resp = requests.get(url, timeout=15, stream=True)   # allow_redirects=True → bypass
```

**Fix:** Disable redirects (`allow_redirects=False`) and reject 3xx, *or* re-validate every hop. Better: bind the resolved public IP into the connection (custom adapter / `requests`' `Session` with a pinned IP) to close the rebinding window. Cheapest correct fix:

```python
resp = requests.get(url, timeout=15, stream=True, allow_redirects=False)
if resp.is_redirect or resp.is_permanent_redirect:
    return None
```

Requires auth to reach, which is why it's High not Critical — but any authenticated admin session (or a CSRF'd one, see SEC-3) can pivot into the host network.

---

## SEC-2 — `os.execv` self-restart is an unauthenticated-adjacent RCE-shaped primitive & a reliability landmine — **MEDIUM** — *before launch*

**Why it matters:** `/api/restart` calls `os.execv(sys.executable, [sys.executable] + sys.argv)` from a daemon thread. It's CSRF-protected and `login_required`, so it's not directly exploitable, but: (a) it re-executes with the *current* `sys.argv`, which in a container is fine but in other launch contexts can re-exec with attacker-influenced arguments if argv was ever derived from env; (b) any bug that lets an unauthenticated caller reach it (e.g. a future endpoint added to the player allowlist by mistake) becomes a DoS/restart loop. More practically it's a **reliability** problem: in-place exec skips OS process supervision, orphans the Waitress socket briefly, and races the grabber pool shutdown (`join(5)` then hard exec — in-flight downloads are abandoned, though startup-recovery mostly compensates).

**File:** `bpm_tagger/web/api/scan.py:151-184`.

**Fix:** Prefer letting the container/supervisor restart the process (exit cleanly, `restart: unless-stopped` in compose). If in-place restart must stay, harden argv and document that it requires a supervisor. At minimum, gate it behind admin-role explicitly (it currently relies only on `login_required`, so a *player* session is blocked only because the endpoint isn't in the allowlist — correct today, fragile tomorrow).

---

## SEC-3 — CSRF cookie is not `Secure` by default; SameSite=Lax is the only cross-site defense — **MEDIUM** — *before launch*

**Why it matters:** `SESSION_COOKIE_SECURE` is set **only** when `ui_public_url` starts with `https://` (`app.py:162`). A very common deployment is "reverse proxy terminates TLS, app sees http, admin forgets to set `UI_PUBLIC_URL`." The session cookie (which *is* the CSRF anchor, since the token lives in the signed session) then rides over any http hop and is exposed to network attackers. SameSite=Lax still allows top-level GET navigations to carry the cookie; combined with any state-changing GET (there are none critical today, but `/api/scan/*` are POST-only, good) it's mostly OK — but the cookie confidentiality gap is real.

**File:** `bpm_tagger/web/app.py:156-163`.

**Fix:** Add an explicit `UI_FORCE_SECURE_COOKIE` env, default it on when behind a proxy (`ui_trusted_proxies > 0`), and document that HTTPS is required for internet exposure. Also set `SESSION_COOKIE_SAMESITE = "Strict"` for the admin session (the SPA is same-origin; Strict costs nothing here and kills the residual CSRF surface).

---

## SEC-4 — No minimum strength / rate-diversity on the **admin** password; env password has no length floor — **MEDIUM** — *before launch*

**Why it matters:** The *run* password enforces ≥8 chars (`settings.py:452`) and must differ from admin. The **admin** password (`UI_PASSWORD` env, or the change-password form) enforces ≥8 only on *change* (`settings.py:499`); the initial env value has no floor and no complexity check. Brute-force lockout is per-IP, in-memory, 5 attempts / 60s window / 300s lockout — reasonable, but resets on restart and is trivially defeated by a botnet (per-IP keying). There is no global attempt cap and no exponential backoff beyond the fixed lockout.

**Files:** `bpm_tagger/web/api/auth.py:23-63`, `bpm_tagger/web/state.py:38-44`.

**Fix:** Enforce a length floor on the env password at boot (warn-and-refuse under 8). Consider a global lockout counter in addition to per-IP. Document that `UI_PASSWORD` must be strong. Low effort, meaningful risk reduction for a publicly-exposed instance.

---

## SEC-5 — `/healthz` leaks library statistics to any authenticated session (including the low-privilege player) — **LOW** — *after launch*

**Why it matters:** `healthz` returns full `get_stats()` for any request where `session.get("ok")` is truthy — including a **player** kiosk session, which is otherwise firewalled to run-only endpoints. It's only aggregate counts (track totals, review counts), not track data, so impact is minor, but it's an authorization inconsistency: the player role is explicitly *not* supposed to see library stats (`api_me` withholds `review_count` from players — `auth.py:93`), yet `healthz` hands them over.

**File:** `bpm_tagger/web/api/media.py:88-97`.

**Fix:** Gate the stats rider on `session.get("role") == "admin"`, not merely `ok`. Keep the bare `status:ok` public for Docker healthchecks.

---

## SEC-6 — Spotify OAuth callback is correctly state-validated, but redirect target is derived from persisted `ui_public_url` — **LOW** — *if time permits*

**Why it matters:** `_redirect_target` builds the post-OAuth redirect from `ui_public_url`. That value is admin-settable and not validated as a same-origin URL, so a mis-set (or maliciously set, by a compromised admin) value could turn the callback into an open redirect. Low because it requires admin write to exploit and only affects the OAuth return.

**File:** `bpm_tagger/web/api/spotify.py:27-29`, `settings.py:277-278`.

**Fix:** Validate `ui_public_url` is a well-formed absolute http(s) origin on save; only ever append known internal paths (it already does — paths are literals), so the residual risk is just malformed config. Low priority.

---

## SEC-7 — Secrets in `settings.json` at 0600 is good; secrets in process env + config dict are broadly readable — **LOW/informational** — *after launch*

**Why it matters:** Good practices observed: `save_settings` writes 0600 and re-chmods; GET `/api/settings` masks the secret keys (`_SECRET_KEYS`); the plaintext-password migration hashes on boot; Spotify client id/secret are env-only and never persisted. The residual issue is that the full config dict (with cleartext Navidrome pass, Deezer ARL, secret key) lives in memory and is passed around widely (`st.config`), and the `test-*` endpoints echo behavior based on secrets. No leak found, but the blast radius of any future `jsonify(st.config)` mistake is total. There's no defense-in-depth (e.g. a secrets sub-object) preventing that class of bug.

**Fix:** Consider isolating secrets into a `Secrets` holder that never serializes. Informational.

**Positive security notes (verified, no action needed):**
- Path traversal is correctly blocked everywhere via `_assert_in_music_dir` (realpath + prefix check) — `state.py:59`. Audio streaming, covers, tags, lyrics all validate.
- SQL is fully parameterized. The dynamic `WHERE` builder (`db.py:490-529`) concatenates only hardcoded clause strings; all user values are bound params. The one `f"ALTER TABLE ... ADD COLUMN {col} {coldef}"` uses a hardcoded migration list, not user input.
- React escapes output; **no** `dangerouslySetInnerHTML`, `eval`, or `innerHTML` anywhere in the SPA.
- CSP is sound: `script-src 'self'` (no unsafe-inline for scripts), scoped img/media allowlist. `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy` present.
- Cover MIME is forced to non-active types (`tracks.py:659`) — a crafted file can't serve `text/html` from origin.
- Password change / run-password change invalidate other sessions via `PW_STAMP` (`auth.py:61-65`) — a genuinely nice design.

---

# 2. PWA / Service Worker

## PWA-1 — Service worker does nothing; "install" flow and update flow are effectively unmanaged — **LOW** — *after launch*

The `sw.js` is a deliberate no-op (install + activate + claim, **zero** fetch interception). This is a *defensible security choice* — the README/comments explain it avoids serving stale authenticated shells and never caches audio. Consequences to accept knowingly:
- **No offline capability** (by design). Fine.
- **No update prompt** — a new deploy is picked up only on full reload; the SW `skipWaiting`/`clients.claim` means the empty worker updates instantly, so there's no stale-SW trap. Good.
- **`start_url: /run`** in the manifest with `scope: /` means the installed PWA opens the player. A player-role kiosk installing the PWA is coherent. No issue.

**No action required** beyond documenting that offline is intentionally unsupported. If you ever add caching, the entire authenticated-shell-staleness class of bugs opens up — resist it.

## PWA-2 — Manifest `id`/`scope` are fine; no `screenshots`/`shortcuts` — **LOW** — *if time permits*
Cosmetic. Add `shortcuts` for Run/Library and `screenshots` for a richer install card. Not a defect.

---

# 3. Architecture

## ARCH-1 — `db.py` is a 1,813-line God object — **MEDIUM** — *after launch*

Every query for tracks, playlists, grab queue, candidates, events, oauth, suggestions, run-stats, star-sync, and play-counts lives in one `BPMDatabase` class. It's readable and consistent, but it's the single highest-churn file and the one most likely to grow merge conflicts and accidental cross-domain coupling. Migrations, the tracks domain, and the grabber domain all share one connection helper and one file.

**Fix (incremental):** Split into `db/tracks.py`, `db/grabber.py`, `db/playlists.py` mixins on a shared `_Connection` base, or repository classes. Do it opportunistically, not as a big-bang rewrite — it's debt, not a bug.

## ARCH-2 — A new SQLite connection per query, no pool — **MEDIUM** — *after launch (watch under load)*

`_connect()` opens a fresh `sqlite3.connect(timeout=30)` on **every** call. With Waitress at 12 threads + up to 3 grab workers + background fill jobs, WAL mode makes concurrent *reads* fine, but writers serialize and the 30s busy-timeout means a burst of writes (bulk star-sync, tag reindex, grab completion) can stack up latency. Connection setup per call also re-runs no PRAGMAs after the first init, so `journal_mode=WAL` is persistent (good) but `foreign_keys` is **never enabled** (see DB-1).

**Fix:** A thread-local connection or a small pool. Low urgency for a single-user library; matters if the library is large (100k+ tracks) or multi-user.

## ARCH-3 — Business logic embedded in API handlers; background jobs held in module-global dicts — **MEDIUM** — *after launch*

The ISRC-fill and lyrics-fill jobs are module-level mutable dicts (`_isrc_fill`, `_fill`) guarded by a module lock (`tracks.py:454`, `lyrics.py:116`). This works for a single process but: (a) it's process-global state in a "stateless" web layer, (b) it can't survive a restart (a fill in progress is silently lost), (c) it can't scale to multiple workers/processes. Similarly, artist-image resolution logic (`_resolve_artist_dir`, `_save_artist_image_to_library`) lives in `tracks.py` and is imported by `images.py` — a cross-blueprint import that signals the logic wants to be in a service module.

**Fix:** Move fill jobs to a small job registry on `AppState` (or reuse the grabber's queue pattern — you already have a durable queue abstraction). Move artist-image + metadata logic into `integrations/` or a `services/` layer.

## ARCH-4 — `web_ui.py` back-compat shim and Jinja-era comments are dead weight — **LOW** — *if time permits*

Comments still reference "the M0 Jinja UI," "the legacy form routes stay intact" (`api/auth.py:6-7`) — but the Jinja UI was removed. Verify no Jinja routes remain and prune the shim if nothing imports it.

---

# 4. Database

## DB-1 — Foreign keys are declared in comments but never enforced; no `PRAGMA foreign_keys=ON` — **MEDIUM** — *before launch*

`grab_candidates.queue_item_id`, `grab_events.queue_item_id`, `playlist_tracks.playlist_id`, `grab_queue.playlist_track_id` are all logical FKs, but **no `FOREIGN KEY` constraints exist** and `PRAGMA foreign_keys` is never turned on (it's off by default in SQLite, per-connection). Deletes rely on manual cascades in Python (`delete_playlist` deletes `playlist_tracks` then `playlists` — `db.py:1310-1311`). Any code path that forgets the manual cascade orphans rows. `grab_candidates` and `grab_events` have **no** cascade on queue-item deletion that I can see — they accumulate.

**Files:** `db.py:198-355` (schema), deletes scattered.

**Fix:** Either add real `FOREIGN KEY ... ON DELETE CASCADE` and enable `PRAGMA foreign_keys=ON` in `_connect()`, or add an explicit periodic orphan-sweep. The pragma-on approach is safest and cheap.

## DB-2 — `grab_events` / `grab_candidates` grow unbounded — **MEDIUM** — *after launch*

Every search writes N candidate rows and every state transition writes an event row (`worker.py:140`, `transition(...)`). There's history retrieval but no retention/pruning. On an active grabber this table grows without limit, slowly bloating the DB and the history queries.

**Fix:** Prune events/candidates for terminal queue items older than N days, or cap per item.

## DB-3 — `file_hash = size:mtime` is fast but collision-prone for change detection — **LOW/informational** — *accepted design*

Documented tradeoff (CLAUDE.md). A file edited in place that preserves size and mtime (e.g. some taggers with `preserve_mtime`) won't be re-detected. That's exactly why `reindex_tags` exists as a manual escape hatch. Acceptable; just know it's a correctness/speed tradeoff, not a bug.

## DB-4 — Migrations are additive-only with a table rebuild for playlists — reasonable, but the rebuild path is fragile — **LOW** — *if time permits*

`_migrate_playlists_schema` does rename→recreate→copy→drop across two passes to relax a NOT NULL/UNIQUE. It preserves row ids (needed for `grab_queue.playlist_track_id`). This is the riskiest migration in the codebase and runs inside the same `_init_db` transaction with a pre-migration `.bak` copy (good). It's correct as written but has no test that exercises "old-shape DB → migrated." A single-version backup (`bak-<version>`) also won't protect across two upgrades in one boot.

**Fix:** Add a migration test with a fixture DB in the pre-generalization shape. Cheap insurance for the one migration that can lose data.

---

# 5. API

## API-1 — Inconsistent error contract: some failures are `200 {ok:false}`, others are HTTP 4xx/5xx `{error}` — **MEDIUM** — *after launch*

`save_bpm` returns `200 {ok:false, error}` on failure (`tracks.py:71,87`); most others return proper status codes. Clients must special-case each endpoint. The `{ok:...}` vs `{error:...}` vs `{ok:..., error:...}` shapes vary. This is a maintainability/DX tax and a source of silent client bugs (the SPA's `api.ts` only triggers the unauthorized handler on 401; a 200-with-ok:false slips through as "success" unless each caller checks `ok`).

**Fix:** Standardize: 4xx/5xx + `{error}` for failures, 2xx + payload for success. It's a breaking change for the SPA, so do it as a coordinated pass.

## API-2 — No API versioning; no request logging/audit trail for state changes — **LOW** — *after launch*
All routes are unversioned `/api/...`. Fine for a coupled SPA+backend shipped together (they always deploy in lockstep), so versioning is low value here. But there's **no audit log** of who changed what (single-user, so "who" is moot, but "what changed when" for destructive ops — trash purge, deleted-purge, password change — would help incident response). Consider an events log for destructive admin actions.

## API-3 — Expensive endpoints have no per-endpoint rate limiting — **LOW** — *after launch*
Only `/api/login` is rate-limited. Waveform computation (librosa), ISRC/lyrics bulk fills, and image search (external API calls) are authenticated but unthrottled. An authenticated-but-careless (or compromised) client can hammer librosa/Deezer. The Deezer limiter (`deezer_limiter`) protects the *upstream* but not local CPU. Low, because auth is required.

**Positive:** The player-role default-deny allowlist (`app.py:62-75`, `_enforce_player_scope`) is excellent API-authorization design — new endpoints are off-limits to players until explicitly listed. Keep this discipline.

---

# 6. Frontend

## FE-1 — `AppState.waveform_cache` (backend) and several frontend patterns aside — the frontend has no tests at all — **MEDIUM** — *before launch* (see TEST-1)

## FE-2 — Very large page components: `Settings.tsx` (1,213), `Run.tsx` (1,202), `TrackDetail.tsx` (905), `player.tsx` (884) — **MEDIUM** — *after launch*

These are monoliths mixing data-fetching, local state, effects, and presentation. `Run.tsx` in particular holds the tempo engine, queue management, localStorage persistence, media-session wiring, and UI. High risk of re-render churn and hard to test. `player.tsx` restores queue from localStorage and mutates it in effects.

**Fix:** Extract hooks (`useRunQueue`, `useTempoLock`, `useMediaSession`) and split presentational components. Do it before adding major Run-mode features.

## FE-3 — localStorage persistence of player/run state is unvalidated on read — **LOW** — *after launch*

`player.tsx:98` does `JSON.parse(localStorage.getItem(SAVE_KEY) || "null")` and restores a queue; `Run.tsx` reads `Number(localStorage.getItem(...))`. A corrupted or hand-edited value could produce `NaN` targets or malformed queue entries. There's some filtering (`survivors`), but no schema validation. Not a security issue (same-origin, user's own storage), just a robustness gap that will surface as a confusing "player won't start" bug.

**Fix:** Validate/clamp on read; wrap in try/catch (some paths already do).

## FE-4 — No global error boundary observed — **MEDIUM** — *before launch*

A thrown render error in any page will white-screen the whole SPA (React unmounts the tree). For a PWA that a runner opens mid-workout, a crash on the Run page is a bad failure mode.

**Fix:** Add a top-level `<ErrorBoundary>` with a "reload" fallback, and a per-route boundary around `Run`.

---

# 7. Performance

- **PERF-1 (LOW):** `api_track` with `back=review` recomputes the *entire* suspicious queue (`get_suspicious(..., 0, inf)`) just to find prev/next indices on every single track-detail load (`tracks.py:247-256`). O(library) per detail view. Cache it or compute neighbors in SQL. *After launch.*
- **PERF-2 (LOW):** `get_track_paths` caps at 5,000 (good), but `_matches` in run-queue scoring loads *all* run candidates and sorts in Python each request (`run.py:86-98`). Fine for typical libraries; watch at 50k+ starred tracks. *After launch.*
- **PERF-3 (LOW):** No HTTP caching on `/api/*` (correct — dynamic), but cover art (`api_track_cover_get`) does proper ETag/304 — good. Artist images use `max_age=86400` — good.
- **PERF-4 (informational):** Bundle is React + react-router + react-query + tailwind — lean, no obvious bloat. Vite code-splitting is available but I didn't see route-level lazy imports; the 4 giant pages ship in the main chunk. Consider `React.lazy` per route. *If time permits.*

---

# 8. Reliability & Concurrency

## REL-1 — `AppState.cache_waveform` mutates a shared dict with **no lock** across 12 Waitress threads — **HIGH** — fix *before launch*

`waveform_cache` is a plain dict written from `cache_waveform` (`state.py:46-51`) and read from multiple request threads. There's an `waveform_inflight_lock` for the *inflight* map but **not** for the cache itself. The eviction path is the dangerous one:

```python
def cache_waveform(self, path, result):
    self.waveform_cache[path] = result
    if len(self.waveform_cache) > self.waveform_cache_max:
        evict = list(self.waveform_cache.keys())[:self.waveform_cache_max // 10]  # ← iterating
        for k in evict:
            self.waveform_cache.pop(k, None)
```

Two threads evicting/inserting concurrently can raise `RuntimeError: dictionary changed size during iteration`, and reads can observe torn state. It'll be rare and load-dependent — exactly the kind of heisenbug that shows up only in production under a busy library grid view.

**Fix:** Guard all cache mutations with a `Lock` (you already import `Lock`), or use a thread-safe structure. Small, high-value.

## REL-2 — Background fill jobs and scans have no persistence across restart — **MEDIUM** — *after launch*
A restart (or the `os.execv` self-restart) mid-fill silently drops the job; the grabber recovers in-flight rows (`GrabPool.start` → `reset_inflight_grabs`) — good — but ISRC/lyrics fills do not. Users will see a job "vanish."

## REL-3 — `subprocess.run(ffmpeg, ...)` has **no timeout** — **MEDIUM** — *before launch*
`transcode.py:59` runs ffmpeg with `check=True, capture_output=True` but no `timeout=`. A malformed/hostile input file (or ffmpeg hang) blocks a grab worker forever, and `capture_output` buffers stderr in memory unbounded. With `grab_workers` small, one stuck transcode can starve the whole pool.

**Fix:** Add `timeout=` (e.g. 300s) and handle `TimeoutExpired` as a failed grab.

## REL-4 — External HTTP calls mostly have timeouts (good), but no retries/backoff on transient failures — **LOW** — *after launch*
Deezer/Spotify/LRCLIB/MusicBrainz calls set timeouts (verified) but a single transient 5xx fails the operation. The grabber retries downloads (2 attempts/candidate) but metadata lookups don't. Acceptable for now.

**Positive:** Timeouts are present on essentially every `requests` call I checked — a common omission this codebase avoids.

---

# 9. Code Quality

- **CQ-1 (LOW):** ~40 broad `except Exception:`/bare handlers, many `# pragma: no cover - best effort`. Most are legitimately best-effort (notifications, cache writes), but a few swallow errors that should surface (e.g. `api_track` quality read, waveform corrupt-value fall-through). Audit the swallow-and-continue sites for ones that hide real failures.
- **CQ-2 (LOW):** Magic numbers sprinkled (waveform cache 500/10%, lockout 5/60/300, MAX_EXCLUDE 500, image 15MB, fill sleeps 0.2/0.25). Mostly self-documenting via comments. Consider a constants module. *If time permits.*
- **CQ-3 (LOW):** Stale comments referencing removed Jinja UI / "M0/M2/M3" milestone shorthand throughout (`app.py`, `auth.py`, `state.py`). Onboarding friction — a new dev has to learn the milestone lingo. Prune.
- **CQ-4 (informational):** Naming and structure are otherwise consistent and good. Blueprints are cleanly separated. The `_check_csrf` + `login_required` decorator pattern is applied uniformly.
- **No** commented-out code blocks, **no** `print()` debugging, only **one** `XXX` (a legitimate ID3 language code, not a marker). Clean.

---

# 10. Developer Experience

- Excellent `CLAUDE.md` / `README` — genuinely above hobby-project norms.
- **DX-1 (MEDIUM):** No Python dependency lockfile. `requirements.txt` uses `>=` ranges for fast-moving libs (`yt-dlp`, `streamrip`, `librosa`) — builds are **non-reproducible**, and a `yt-dlp` upstream change can silently break the grabber between two identical `docker build`s. The frontend *does* have `package-lock.json` (good). *Before launch.*
- **DX-2 (LOW):** No `CONTRIBUTING.md`, no dev-container, but `run.ps1` + compose cover the common paths.

---

# 11. Dependencies

- **DEP-1 (MEDIUM, before launch):** No dependency vulnerability scanning in CI (no `pip-audit`, `npm audit`, Dependabot, or `safety`). For a Docker-published public project this is table stakes.
- **DEP-2 (MEDIUM):** `yt-dlp>=2024.8.0` and `streamrip>=2.1.0` are unpinned and fragile-by-nature (they break when sites change). Pin exact versions in a lockfile and update deliberately (see DX-1). `streamrip` in particular pulls a large async/aiohttp tree and is the heaviest dependency for the value delivered.
- **DEP-3 (LOW):** `pillow>=10,<11` is pinned sensibly. `librosa`/`numpy`/`soundfile` are heavy but core to the product. No obvious redundant/overlapping packages. `rapidfuzz` for matching is a good choice.
- **DEP-4 (informational):** Legal/ToS risk (not a code issue): the grabber downloads from Deezer (via ARL) and YouTube. That's the user's own account/risk, and it's opt-in and off by default — but a public project shipping this should keep the "your account, your responsibility" framing explicit in docs.

---

# 12. Testing

- **TEST-1 (HIGH, before launch):** **Zero frontend tests.** No vitest/jest/testing-library. The Run page (tempo math, octave folding, queue refill) and the player are the most logic-dense, most user-visible code and are entirely untested. The octave-fold/rate math (`run.py:_fold`, and its client mirror) is exactly the kind of arithmetic that regresses silently.
- **TEST-2 (MEDIUM):** Backend has a solid 31-file pytest suite covering matching, reconcile, normalize, providers, tag index, API happy-paths, sync. Gaps: **auth/session/CSRF** logic (the security-critical `login_required`/`_enforce_player_scope`/`PW_STAMP` invalidation) has only incidental coverage; the **playlist schema migration** (DB-4) is untested; **concurrency** (REL-1) is untested (hard to, but a stress test would catch it).
- **TEST-3 (LOW):** No e2e (Playwright is available in the environment). A single smoke test — login → list tracks → play → run-queue — would catch integration breaks the unit tests miss.

---

# Scores

| Dimension | Score (1–10) | One-line justification |
|---|---|---|
| **Overall Architecture** | **6.5** | Clean blueprint separation and a nice role-scope model, dragged down by a 1.8k-line DB god-object, per-call connections, and logic-in-handlers. |
| **Security** | **6** | Strong fundamentals (CSRF, path guards, default-deny roles, secret masking, SSRF *awareness*) but real gaps: SSRF redirect bypass, cookie-Secure default, weak admin-password floor. Good for single-user LAN; not yet for public exposure. |
| **Maintainability** | **6.5** | Excellent docs and consistent style; hurt by oversized files, stale milestone comments, and inconsistent API error contracts. |
| **Performance** | **7** | Sensible caching (ETags, waveform cache, Deezer limiter), lean bundle. O(library) hotspots in review-nav and run-scoring are the only real watch items. |
| **Scalability** | **5** | Explicitly single-process/single-user: SQLite with per-call connections, in-memory job state, in-memory lockout, `os.execv` restart. Fine for its niche, hard ceiling beyond it. |
| **Production Readiness** | **5.5** | Ready for its **intended** niche (self-hosted, single admin, behind a proxy) after a short must-fix list. **Not** ready for untrusted/public multi-user deployment as-is. |

---

## Top 10 Highest-Risk Issues
1. **REL-1** — Unlocked shared `waveform_cache` mutation across threads (crash/torn state under load). *HIGH*
2. **SEC-1** — SSRF via redirect past the public-host guard (LAN/metadata pivot). *HIGH*
3. **TEST-1** — No frontend tests around the most logic-dense, user-facing code. *HIGH*
4. **REL-3** — `ffmpeg` subprocess without timeout (worker starvation). *MEDIUM*
5. **DB-1** — Foreign keys never enforced (`PRAGMA foreign_keys` off) → orphan rows / data drift. *MEDIUM*
6. **SEC-3** — Session/CSRF cookie not `Secure` by default on http-behind-proxy deploys. *MEDIUM*
7. **SEC-4** — No admin-password strength floor; per-IP-only lockout. *MEDIUM*
8. **DEP-1/DEP-2** — No dependency vuln scanning + unpinned fragile deps (yt-dlp/streamrip). *MEDIUM*
9. **FE-4** — No React error boundary → whole-app white-screen on any render error. *MEDIUM*
10. **DB-2** — `grab_events`/`grab_candidates` grow unbounded. *MEDIUM*

## Top 10 Easiest High-Impact Improvements
1. Add a `Lock` around `waveform_cache` mutations (REL-1) — ~5 lines, kills a HIGH.
2. `allow_redirects=False` + reject 3xx in `_fetch_image` and artist-image fetch (SEC-1) — ~3 lines.
3. `timeout=300` on the ffmpeg `subprocess.run` (REL-3) — 1 line + one except branch.
4. Enable `PRAGMA foreign_keys=ON` in `_connect()` and/or add cascade sweeps (DB-1).
5. Gate `/healthz` stats on admin role (SEC-5) — 1 condition.
6. Force `Secure` cookie when `ui_trusted_proxies > 0` (SEC-3).
7. Add a top-level `<ErrorBoundary>` (FE-4).
8. Add `pip-audit` + `npm audit` (or Dependabot) to `ci.yml` (DEP-1).
9. Enforce an admin-password length floor at boot (SEC-4).
10. Add a Python lockfile (`pip-compile` / `uv lock`) and pin yt-dlp/streamrip (DX-1/DEP-2).

## Technical debt that can wait (after launch)
`db.py` split (ARCH-1), connection pooling (ARCH-2), fill-jobs → durable registry (ARCH-3/REL-2), API error-contract unification (API-1), large-component refactors (FE-2), event/candidate pruning (DB-2), review-nav O(library) fix (PERF-1), stale-comment cleanup (CQ-3).

## Technical debt that should NOT wait (before launch)
REL-1 (cache lock), SEC-1 (SSRF), REL-3 (ffmpeg timeout), DB-1 (FK enforcement), SEC-3/SEC-4 (cookie/password hardening), FE-4 (error boundary), DEP-1 (dep scanning), TEST-1 (at least a Run-math + auth-flow test), migration test (DB-4).

## Likely to cause bugs in the next six months
- The unlocked waveform cache **will** throw under a busy library grid (REL-1).
- `yt-dlp`/`streamrip` **will** break on an upstream change with no pin to hold the line (DEP-2).
- The playlist migration path is a latent data-loss risk with no test (DB-4).
- Inconsistent API error shapes will cause the SPA to treat a failure as success somewhere (API-1).
- `grab_events`/`grab_candidates` growth will quietly slow the grabber DB (DB-2).

## Likely to slow future feature development
- `db.py`, `Settings.tsx`, `Run.tsx`, `player.tsx` monoliths (ARCH-1, FE-2) — every new feature touches them and risks conflicts/regressions.
- Logic-in-handlers + cross-blueprint imports (ARCH-3) — no service layer to hang new behavior on.
- No frontend tests (TEST-1) — every UI change is a manual-QA event, which throttles iteration speed.

---

## Would I approve this for production deployment as-is?

**Conditionally. For its intended niche — a single self-hoster running it on a LAN or behind an authenticating reverse proxy — YES, after the "should not wait" list above (a focused day or two of work).** The security fundamentals are genuinely good for a project of this size, and nothing I found is a smoking-gun unauthenticated RCE or auth bypass.

**For a public, internet-facing, or multi-user deployment — NO, not as-is.** The SSRF redirect gap (SEC-1), the default-non-Secure cookie (SEC-3), the absent admin-password floor (SEC-4), and the unlocked shared cache that crashes under concurrency (REL-1) are collectively disqualifying for an untrusted-network posture. Fix those four plus add dependency scanning and a minimal test safety net, and it moves to a defensible "yes."

The gap between those two answers is small and well-defined — this is a **fixable** codebase, not a rewrite candidate. The single most important cultural fix is **testing the frontend and the auth path**, because right now the two things most likely to break (Run-mode math and session/role enforcement) are the two things with the least automated coverage.
