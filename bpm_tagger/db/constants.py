"""Shared constants for the db package."""

# Grab-queue status state machine (§2). Non-terminal states mean the item is
# still "in flight"; exactly one non-terminal item may exist per spotify_track_id.
GRAB_TERMINAL = ("done", "failed", "skipped")
GRAB_NONTERMINAL = ("pending", "searching", "awaiting_user", "downloading",
                    "transcoding", "tagging", "analyzing_bpm")

# Library listing sort keys → the ORDER BY they select. "" is the long-standing
# default (newest-analyzed first) and stays the fallback for anything unknown, so
# a stale or hand-typed ?sort= can never change what the table has always shown.
# The alternatives end on the same deterministic tiebreakers as the default, so
# paging a leaderboard-style order can't skip or repeat a row. Lives here (not in
# tracks.py) because the web layer validates ?sort= against the same keys.
_ADMIN_RATING_SORT = ("(SELECT r.rating FROM track_ratings r "
                     "WHERE r.track_id = tracks.id AND r.owner = 'admin')")

TRACK_SORTS = {
    "": "analyzed_at DESC",
    "plays": "COALESCE(play_count, 0) DESC, analyzed_at DESC, file_path",
    "plays_asc": "COALESCE(play_count, 0) ASC, analyzed_at DESC, file_path",
    # The admin's own rating (D19); unrated sorts last either direction, via
    # SQLite's NULLS LAST, so switching direction never hides unrated tracks
    # at the top.
    "rating": f"{_ADMIN_RATING_SORT} DESC NULLS LAST, analyzed_at DESC, file_path",
    "rating_asc": f"{_ADMIN_RATING_SORT} ASC NULLS LAST, analyzed_at DESC, file_path",
}
