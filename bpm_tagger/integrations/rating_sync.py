"""Two-way Navidrome rating sync (admin only) — opt-in, its own phase.

See docs/plans/ratings-weighted-picking.md § "Navidrome rating sync" (D15/D16).
Mirrors integrations/star_sync.py: a three-way merge per track between
``local`` (the admin's rating now), ``remote`` (that song's ``userRating`` on
the server now) and ``base`` (``track_ratings.rating_base`` — what both sides
agreed on at the last successful sync). Only a side that differs from base
counts as "changed"; unlike the boolean star flag, an int rating CAN reach a
genuine both-changed-and-disagreeing conflict, which resolves to local (D16 —
ratings are "moderate" but local is the account whose ratings actually get
synced, so it wins).

Matching reuses star_sync's ``_match_remote_to_local`` against the same
``iter_all_songs`` walk play_sync already uses for play counts (each song
object already carries ``userRating``), so running both pulls in one
PeriodicSync tick costs one walk, not two.

The sync baseline (``rating_base``) advances ONLY when any required remote
write succeeded, so a failed push retries on the next run instead of being
silently dropped — same contract as the star sync.
"""

import logging

from .navidrome import iter_all_songs, resolve_id, set_rating
from .star_sync import _match_remote_to_local

log = logging.getLogger(__name__)


def merge_rating(local, remote, base):
    """Pure three-way merge for one track's admin rating (None or 1-5).

    Returns ``(final, action)`` with action one of ``none`` (already agree),
    ``push`` (local changed → remote follows), ``pull`` (remote changed →
    local follows) or ``conflict`` (both changed, disagreeing → local wins,
    D16). A None ``base`` (first sync, or a track never synced before) falls
    out of the same formula: only one side differs from None → that side is
    "changed" and wins; both differ from None (and each other) → conflict →
    local wins."""
    if local == remote:
        return local, "none"
    local_changed = local != base
    remote_changed = remote != base
    if local_changed and not remote_changed:
        return local, "push"
    if remote_changed and not local_changed:
        return remote, "pull"
    return local, "conflict"  # both changed and disagree → local wins (D16)


def sync_ratings(db, config: dict) -> dict:
    """One full reconciliation pass over the admin's ratings. Returns a counts
    dict for the UI toast: ``{ok, remote_songs, matched, pulled, pushed,
    conflicts, errors}`` or ``{ok: False, error}`` when the remote can't be
    reached at all."""
    url = str(config.get("navidrome_url", "")).rstrip("/")
    user = str(config.get("navidrome_user", ""))
    pwd = str(config.get("navidrome_pass", ""))
    if not (url and user and pwd):
        return {"ok": False, "error": "Navidrome URL, username and password must be configured first."}

    try:
        remote_songs = list(iter_all_songs(url, user, pwd))
    except Exception as exc:
        return {"ok": False, "error": f"Could not list Navidrome songs: {exc}"}

    rows = db.all_admin_ratings_for_sync()
    matched = _match_remote_to_local(rows, remote_songs, db)
    counts = {"ok": True, "remote_songs": len(remote_songs), "matched": len(matched),
              "pulled": 0, "pushed": 0, "conflicts": 0, "errors": 0}

    for row in rows:
        song = matched.get(row["file_path"])
        local, base = row["rating"], row["rating_base"]
        if song is None:
            # Not matched in this walk: the remote rating is UNKNOWN, not
            # cleared — pulling "None" would wipe a local rating whenever
            # matching fails. Only a local change may go out (push).
            if local == base:
                continue
            final, action = local, "push"
            remote = object()  # sentinel: always differs from final → write
        else:
            remote = song.get("userRating") or None
            final, action = merge_rating(local, remote, base)
        if action == "none":
            continue

        if final != remote:
            # Remote must change — resolve an id via search3 when the match
            # didn't come with one (a rating we're pushing to a song this
            # walk didn't carry a userRating for, or a track absent from the
            # walk's path/metadata match).
            sid = (song or {}).get("id") or row.get("nd_song_id")
            if not sid:
                try:
                    sid = resolve_id(url, user, pwd, row)
                except Exception:
                    sid = None
            if not (sid and set_rating(url, user, pwd, sid, final or 0)):
                counts["errors"] += 1
                continue  # baseline untouched → retried next run
            song = song or {"id": sid}

        db.set_rating_synced(row["file_path"], final, nd_song_id=(song or {}).get("id"))
        if action == "push":
            counts["pushed"] += 1
        elif action == "pull":
            counts["pulled"] += 1
        elif action == "conflict":
            counts["conflicts"] += 1

    log.info("Rating sync: %(remote_songs)d remote songs, %(matched)d matched, "
              "%(pushed)d pushed, %(pulled)d pulled, %(conflicts)d conflicts, "
              "%(errors)d errors", counts)
    return counts
