# Plan: Two-way star sync with Navidrome

> Current status of all plans is tracked in [STATUS.md](STATUS.md).

Status: implemented (v2.5.2, 2026-07-14) — schema, Subsonic client, merge driver
(`integrations/star_sync.py`), `POST /api/settings/sync-stars`, Settings toggle +
"Sync stars now" button, tests (`tests/test_star_sync.py`).

Implementation notes that amend the plan below:

- **Conflicts are unreachable in v1.** With a per-track *boolean* baseline,
  `local != remote` forces `base` to equal one side, so exactly one side ever
  counts as "changed" — every disagreement resolves as a push or a pull. The
  conflict branch and the star-wins policy exist in `merge_star()` (defensive,
  and ready for a future `starred_at` timestamp merge where real conflicts
  appear), but no `navidrome_star_policy` config/UI knob was added: it could
  never do anything.
- **No `navidrome_path_strip` config either** — `_paths_match()` does
  segment-aligned suffix matching, which absorbs differing container roots
  without configuration; the remote→local pass indexes by filename so it stays
  O(local + remote).
- Remote stars that match no local row are counted as `unmatched_remote` in the
  result (surfaced in the Settings toast) rather than a separate mapping test.

## Goal

Keep the `starred` flag in the BPM Tagger DB and the "loved" (starred) state in
Navidrome in agreement, in **both directions**, so that:

- stars you set in Navidrome show up in BPM Tagger's Run-mode queue (which already
  prefers starred tracks), and
- stars you set in BPM Tagger's UI propagate back out to Navidrome.

**v1 scope: manual only.** A "Sync stars now" button in Settings triggers one full
reconciliation pass. No periodic/background sync in v1 (easy to add later — see
"Future"). Play-count import is explicitly **out of scope** for this plan (tracked
separately; it needs full-library paging, whereas star sync does not).

## Conflict policy: star-wins

When both sides changed the same track since the last sync and disagree, **a star wins
over an un-star** (union semantics). Rationale: for a music library a star is rarely
regretted, and losing one to a stale un-star is the more annoying failure. The policy is
a config value (`navidrome_star_policy`, default `star_wins`; other values `remote_wins`
/ `local_wins` reserved for later) so the merge code stays policy-agnostic.

True newest-wins is **not possible in v1**: Subsonic exposes a `starred` timestamp on the
remote side, but there is no local star timestamp. A `starred_at` column could be added
later to enable it.

## Why this is a three-way merge (read this first)

A naive "make both sides equal" cannot tell these two situations apart:

- track starred **in BPM Tagger** since last sync (should push OUT), vs.
- track un-starred **in Navidrome** since last sync (should pull the un-star IN).

Both present as "local starred, remote not". Distinguishing them requires a **baseline**:
the value both sides agreed on at the end of the previous sync. The merge compares three
values per track — `local` (now), `remote` (now), `base` (last agreed) — and only a side
that *differs from base* is considered "changed".

## Identity mapping: local row → Navidrome song

The Subsonic API keys on an opaque song `id`; BPM Tagger keys on `file_path`. Resolve
with a fast path + fallback:

1. **By path suffix.** `getStarred2` / `search3` return each song's `path`. Navidrome's
   path root (e.g. `/music/...` inside its container) may differ from BPM Tagger's
   `MUSIC_DIR` root, so compare by **suffix** after stripping configurable roots
   (`navidrome_path_strip`, default = strip nothing; fall back to longest-common-suffix
   match).
2. **By metadata fallback.** When paths don't line up, reuse the grabber's proven fuzzy
   matcher — `grabber/matching.py` `score()` / `library_match()` (title + artist +
   duration, ISRC short-circuit). Same threshold semantics as the grabber.

Cache the resolved id in `tracks.nd_song_id` so re-syncs skip re-resolution.

## Architecture

```
bpm_tagger/integrations/navidrome.py   EXTEND — add get_starred / resolve_id / set_star (Subsonic client)
bpm_tagger/integrations/star_sync.py   NEW    — sync_stars() driver (three-way merge + policy)
bpm_tagger/web/api/settings.py         EXTEND — POST /api/settings/sync-stars route (CSRF-guarded)
bpm_tagger/db.py                       + 2 columns, + 2 helper methods
bpm_tagger/config.py                   + navidrome_star_sync / navidrome_star_policy / navidrome_path_strip
frontend/src/...(Settings page)        + "Sync stars now" button → result toast
```

No new blueprint — the route lives in the existing `settings` API. No new service thread
in v1 (manual trigger runs inline on the request's Waitress worker; a full library's
star set is one HTTP call plus a per-changed-track push, so it completes quickly).

## Schema additions (additive, matches existing `_migrate()` pattern)

Add to the `ALTER TABLE tracks ADD COLUMN` list in `db.py` `_migrate()`:

```python
("nd_song_id",   "TEXT"),               # cached Navidrome/Subsonic song id
("starred_base", "INTEGER DEFAULT 0"),  # remote 'starred' at last successful sync (baseline)
```

- `starred`       — local truth now (already exists).
- `starred_base`  — what both sides agreed on last successful sync.
- remote-now      — fetched fresh from `getStarred2`, not stored.

**First-run behaviour:** `starred_base` defaults to 0, so the first sync sees every remote
star as "remote changed → pull in" and every local star as "local changed → push out".
That is the correct union bootstrap, and it produces **no conflicts** (base is uniformly
0, so at most one side differs from base per track).

New DB helpers:

```python
def all_tracks_for_star_sync(self) -> list[dict]:
    # file_path, starred, starred_base, nd_song_id, title, artist, album,
    # duration_ms, isrc, norm_title, norm_artist  WHERE status != 'deleted'

def set_star_synced(self, file_path, starred: bool, nd_song_id: str | None):
    # writes starred = ?, starred_base = ?  (advance baseline only on success),
    # and nd_song_id if newly resolved
```

## Subsonic client (extend `integrations/navidrome.py`)

Reuse the existing salt+token auth pattern already in `ping_navidrome`.

```python
def _sub_params(user, pwd):
    salt = secrets.token_hex(6)
    return {"u": user, "t": hashlib.md5((pwd + salt).encode()).hexdigest(),
            "s": salt, "v": "1.8.0", "c": "bpm-tagger", "f": "json"}

def get_starred(url, user, pwd) -> list[dict]:
    """Every currently-starred song: [{id, path, title, artist, album, duration}].
    ONE call returns the entire remote starred set — no library paging needed."""
    r = requests.get(f"{url}/rest/getStarred2", params=_sub_params(user, pwd), timeout=30)
    r.raise_for_status()
    return (r.json().get("subsonic-response", {}).get("starred2", {}) or {}).get("song", []) or []

def resolve_id(url, user, pwd, track) -> str | None:
    """For pushing a local star OUT: find the Navidrome id via search3,
    confirm by path suffix, else best matching.score() over the hits."""
    q = f'{track.get("artist","")} {track.get("title","")}'.strip()
    r = requests.get(f"{url}/rest/search3",
                     params={**_sub_params(user, pwd), "query": q, "songCount": 20}, timeout=20)
    hits = (r.json().get("subsonic-response", {}).get("searchResult3", {}) or {}).get("song", []) or []
    return best_match_id(track, hits)   # path-suffix first, then matching.score threshold

def set_star(url, user, pwd, song_id, starred: bool) -> bool:
    ep = "star" if starred else "unstar"
    r = requests.get(f"{url}/rest/{ep}", params={**_sub_params(user, pwd), "id": song_id}, timeout=15)
    return (r.json().get("subsonic-response", {}).get("status") == "ok")
```

**Asymmetry that keeps it cheap:** *pull* needs zero paging (`getStarred2` is one call and
carries paths). *Push* needs a per-track `search3` — but only for tracks whose star
actually changed locally, which is normally a small set.

## The three-way merge

For each matched track, with `local`=`starred`, `remote`=(id ∈ getStarred2), `base`=`starred_base`:

| local | remote | base | action |
|---|---|---|---|
| == remote | | (any) | nothing; `final = local` |
| ≠ base | == base | | **push** local → remote (`set_star`); `final = local` |
| == base | ≠ base | | **pull** remote → local; `final = remote` |
| ≠ base | ≠ base (and local ≠ remote) | | **conflict** → `apply_policy` (star_wins ⇒ `final = True`); reconcile the side that loses |

After resolving, `set_star_synced(file_path, final, nd_song_id=rid)` writes `starred` and
advances `starred_base = final`. **Advance the baseline only for tracks whose remote write
(if any) actually succeeded**, so a failed `set_star` retries next run instead of being
silently dropped.

Driver skeleton (`integrations/star_sync.py`):

```python
def sync_stars(db, config) -> dict:
    url  = config["navidrome_url"].rstrip("/"); user = config["navidrome_user"]; pwd = config["navidrome_pass"]
    remote = get_starred(url, user, pwd)
    remote_by_id, remote_paths = index_remote(remote)     # id set + path→id
    counts = {"pushed": 0, "pulled": 0, "conflicts": 0, "unmatched": 0, "failed": 0}
    for row in db.all_tracks_for_star_sync():
        rid = match_remote(row, remote_paths, remote, config)  # path suffix → fuzzy fallback
        remote_starred = rid is not None and rid in remote_by_id
        local, base = bool(row["starred"]), bool(row["starred_base"])
        if local == remote_starred:
            final = local
        elif local != base and remote_starred == base:               # push
            sid = row["nd_song_id"] or (rid if remote_starred else resolve_id(url, user, pwd, row))
            if sid and set_star(url, user, pwd, sid, local):
                final = local; counts["pushed"] += 1; rid = sid
            else:
                counts["failed"] += 1; continue                       # leave baseline; retry next run
        elif remote_starred != base and local == base:               # pull
            final = remote_starred; counts["pulled"] += 1
        else:                                                         # conflict
            final = apply_policy(config, local, remote_starred)       # star_wins ⇒ True
            if final != remote_starred:                               # bring remote up to final
                sid = rid or resolve_id(url, user, pwd, row)
                if not (sid and set_star(url, user, pwd, sid, final)):
                    counts["failed"] += 1; continue
                rid = sid
            counts["conflicts"] += 1
        db.set_star_synced(row["file_path"], final, nd_song_id=rid)
    return counts
```

## Web wiring

- **Route:** `POST /api/settings/sync-stars` in the existing `web/api/settings.py`
  blueprint. CSRF-guarded like every other state-changing route. Returns the `counts`
  dict as JSON.
- **UI:** a "Sync stars now" button on the Settings page (near the existing Navidrome
  connection fields), disabled unless Navidrome creds are set. On click → POST → toast:
  "Pulled N, pushed M, K conflicts (star-wins), U unmatched".
- **Config:** `navidrome_star_sync` (bool, gates the button visibility), `navidrome_star_policy`
  (default `star_wins`), `navidrome_path_strip` (path-root normalization). Persisted to
  `settings.json` like all other settings; reuse existing `navidrome_url/user/pass`.

## Edge cases

- **Remote star matches no local file** → count as `unmatched`, skip (logged). Likely a
  path-root mismatch — see mapping test below.
- **Local file deleted/renamed** → `status='deleted'` rows are excluded by
  `all_tracks_for_star_sync()`; nothing to do.
- **Path-root mismatch is the most likely real-world snag.** Add a lightweight
  "test mapping" affordance: report how many of `getStarred2`'s paths resolved to a local
  row, surfaced in the Settings UI so the user can tune `navidrome_path_strip`.
- **Per-write robustness:** wrap each `set_star` so one failure doesn't abort the run;
  only advance `starred_base` on success (failures retry next run automatically).
- **Duplicates:** if two local rows match the same Navidrome id (dupe files), both get the
  remote value on pull; on push, last-writer-wins to remote (harmless — same id, same flag).

## Testing

- Unit-test the three-way merge as a pure function over `(local, remote, base, policy)` →
  `(final, action)` — all 8 truth-table rows + both non-default policies.
- Mock the Subsonic client (`get_starred` / `resolve_id` / `set_star`) and assert the
  driver's `counts` and the `set_star_synced` calls for a small fixture library covering:
  first-run bootstrap, pure pull, pure push, conflict, unmatched, write-failure-no-baseline.
- Fits the existing pytest suite (`tests/`, `conftest.py` provides a temp DB).

## Future (out of scope for v1)

- Periodic/background sync (piggy-back on the `startScan` moment, or a dedicated interval
  thread) once the manual path is proven.
- `starred_at` column → enable a real newest-wins policy.
- Play-count import (`getSong`/`getAlbumList2` paging → new `play_count` column) to feed
  Run-mode queue ordering — separate plan.
