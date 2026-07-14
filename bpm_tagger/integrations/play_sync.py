"""One-way play-count pull from Navidrome into the local DB.

Navidrome is the source of truth for plays: every Subsonic client scrobbles into
it (including BPM Tagger's own player, via /api/scrobble), so a pull is a plain
overwrite — no merge or baseline needed, unlike the star sync. One full-library
walk via search3 paging brings back every song with its `playCount`; songs are
claimed by the same matcher the star sync uses (cached id → path suffix → fuzzy
metadata), and matched rows get play_count / last_played / a cached nd_song_id
in one bulk write.
"""

import logging

from .navidrome import iter_all_songs
from .star_sync import _match_remote_to_local

log = logging.getLogger(__name__)


def pull_play_counts(db, config: dict) -> dict:
    """One full pull. Returns a counts dict for the UI toast:
    ``{ok, remote_songs, matched, updated, unmatched_remote}`` or
    ``{ok: False, error}`` when the remote can't be reached."""
    url = str(config.get("navidrome_url", "")).rstrip("/")
    user = str(config.get("navidrome_user", ""))
    pwd = str(config.get("navidrome_pass", ""))
    if not (url and user and pwd):
        return {"ok": False, "error": "Navidrome URL, username and password must be configured first."}

    try:
        remote_songs = list(iter_all_songs(url, user, pwd))
    except Exception as exc:
        return {"ok": False, "error": f"Could not list Navidrome songs: {exc}"}

    rows = db.all_tracks_for_star_sync()  # same fields the matcher needs
    matched = _match_remote_to_local(rows, remote_songs, db)

    updates = []
    for path, song in matched.items():
        updates.append((path,
                        int(song.get("playCount") or 0),
                        song.get("played") or None,   # OpenSubsonic last-played timestamp
                        song.get("id") or None))
    updated = db.set_play_counts(updates)

    matched_ids = {s.get("id") for s in matched.values()}
    counts = {"ok": True, "remote_songs": len(remote_songs),
              "matched": len(matched), "updated": updated,
              "unmatched_remote": sum(1 for s in remote_songs
                                      if s.get("id") not in matched_ids)}
    log.info("Play-count pull: %(remote_songs)d remote songs, %(matched)d matched, "
             "%(updated)d updated, %(unmatched_remote)d unmatched", counts)
    return counts
