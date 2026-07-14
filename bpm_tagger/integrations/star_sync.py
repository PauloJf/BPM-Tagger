"""Two-way star sync between the local DB and Navidrome.

See docs/plans/navidrome-star-sync.md. The core is a three-way merge per track:
``local`` (tracks.starred now), ``remote`` (is the song in getStarred2 now?) and
``base`` (tracks.starred_base — what both sides agreed on at the last successful
sync). Only a side that differs from base counts as "changed"; that is what lets
"starred here since last sync" be told apart from "unstarred there since last
sync". Conflicts (both changed, disagreeing) resolve by policy — the default
star_wins treats a star as never regrettable and keeps it.

Matching is asymmetric to stay cheap at library scale:

* remote → local: one getStarred2 call carries every starred song WITH its path;
  cached ids and segment-aligned path suffixes claim most rows for free, and the
  leftovers go through the grabber's SQL-prefiltered fuzzy library_match().
* local → remote: only rows whose star actually needs pushing resolve an id via
  search3 — normally a handful.

The sync baseline advances ONLY when any required remote write succeeded, so a
failed star/unstar retries on the next run instead of being silently dropped.
"""

import logging

from ..grabber.matching import library_match
from .navidrome import _norm_path, _paths_match, _to_match_dict, get_starred, resolve_id, set_star

log = logging.getLogger(__name__)

POLICIES = ("star_wins", "local_wins", "remote_wins")


def merge_star(local: bool, remote: bool, base: bool, policy: str = "star_wins") -> tuple[bool, str]:
    """Pure three-way merge for one track's star flag.

    Returns ``(final, action)`` with action one of ``none`` (already agree),
    ``push`` (local changed → remote follows), ``pull`` (remote changed → local
    follows) or ``conflict`` (both changed, disagreeing → policy decides).
    """
    if local == remote:
        return local, "none"
    local_changed = local != base
    remote_changed = remote != base
    if local_changed and not remote_changed:
        return local, "push"
    if remote_changed and not local_changed:
        return remote, "pull"
    # Both-changed-and-disagreeing is UNREACHABLE for a boolean flag with a
    # trustworthy baseline: local != remote forces base to equal one of them,
    # so exactly one side "changed". This branch is defensive (a corrupted
    # baseline) and ready for a future timestamp-based merge where genuine
    # conflicts exist; the policy default keeps the star (union semantics).
    if policy == "local_wins":
        return local, "conflict"
    if policy == "remote_wins":
        return remote, "conflict"
    return True, "conflict"  # star_wins


def _match_remote_to_local(rows: list[dict], remote_songs: list[dict], db) -> dict[str, dict]:
    """Map local file_path → remote song for every starred remote song we can
    claim. Cheap passes first (cached id, filename + path suffix), then fuzzy
    library_match for whatever the path couldn't line up."""
    by_id = {s["id"]: s for s in remote_songs if s.get("id")}
    # filename → [songs] so the path pass is O(local + remote), not a cross join.
    by_fname: dict[str, list[dict]] = {}
    for s in remote_songs:
        fname = _norm_path(s.get("path", "")).rsplit("/", 1)[-1]
        if fname:
            by_fname.setdefault(fname, []).append(s)

    matched: dict[str, dict] = {}
    claimed_ids: set[str] = set()
    for row in rows:
        song = None
        rid = row.get("nd_song_id")
        if rid and rid in by_id:
            song = by_id[rid]
        else:
            fname = _norm_path(row["file_path"]).rsplit("/", 1)[-1]
            for cand in by_fname.get(fname, []):
                if _paths_match(row["file_path"], cand.get("path", "")):
                    song = cand
                    break
        if song and song.get("id"):
            matched[row["file_path"]] = song
            claimed_ids.add(song["id"])

    # Fuzzy fallback for remote stars whose path didn't line up with any local
    # row (path-root mismatch, moved files). library_match rides the norm-column
    # SQL prefilter, so this is O(remaining remote), not O(local × remote).
    for s in remote_songs:
        sid = s.get("id")
        if not sid or sid in claimed_ids:
            continue
        path = library_match(_to_match_dict(s), db)
        if path and path not in matched:
            matched[path] = s
            claimed_ids.add(sid)
    return matched


def sync_stars(db, config: dict) -> dict:
    """One full reconciliation pass. Returns a counts dict for the UI toast:
    ``{ok, checked, pushed, pulled, conflicts, unmatched_remote, failed}`` or
    ``{ok: False, error}`` when the remote can't be reached at all."""
    url = str(config.get("navidrome_url", "")).rstrip("/")
    user = str(config.get("navidrome_user", ""))
    pwd = str(config.get("navidrome_pass", ""))
    if not (url and user and pwd):
        return {"ok": False, "error": "Navidrome URL, username and password must be configured first."}
    policy = str(config.get("navidrome_star_policy", "star_wins"))
    if policy not in POLICIES:
        policy = "star_wins"

    try:
        remote_songs = get_starred(url, user, pwd)
    except Exception as exc:
        return {"ok": False, "error": f"Could not fetch Navidrome starred songs: {exc}"}

    rows = db.all_tracks_for_star_sync()
    matched = _match_remote_to_local(rows, remote_songs, db)
    matched_ids = {s["id"] for s in matched.values()}
    counts = {"ok": True, "checked": len(rows), "pushed": 0, "pulled": 0,
              "conflicts": 0, "failed": 0,
              # Remote stars no local row claimed — usually a path-root mismatch
              # or files not in this library; surfaced so it never fails silently.
              "unmatched_remote": sum(1 for s in remote_songs if s.get("id") not in matched_ids)}

    for row in rows:
        song = matched.get(row["file_path"])
        remote = song is not None
        local, base = bool(row["starred"]), bool(row["starred_base"])
        final, action = merge_star(local, remote, base, policy)

        if final != remote:
            # Remote must change — star (song absent from the starred set) needs
            # an id via search3; unstar already carries one from the match.
            sid = (song or {}).get("id") or row.get("nd_song_id")
            if not sid:
                try:
                    sid = resolve_id(url, user, pwd, row)
                except Exception:
                    sid = None
            if not (sid and set_star(url, user, pwd, sid, final)):
                counts["failed"] += 1
                continue  # baseline untouched → retried next run
            song = song or {"id": sid}

        db.set_star_synced(row["file_path"], final, nd_song_id=(song or {}).get("id"))
        if action == "push":
            counts["pushed"] += 1
        elif action == "pull":
            counts["pulled"] += 1
        elif action == "conflict":
            counts["conflicts"] += 1

    log.info("Star sync: %(checked)d checked, %(pushed)d pushed, %(pulled)d pulled, "
             "%(conflicts)d conflicts, %(unmatched_remote)d unmatched remote, "
             "%(failed)d failed", counts)
    return counts
