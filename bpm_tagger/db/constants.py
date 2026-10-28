"""Shared constants for the db package."""

# The per-artist "tempo mix" the Artists page draws: this many equal bins across
# [MIX_LO, MIX_HI] BPM, with anything outside folded into the end bins. Fixed here
# (and echoed to the client as ``mix_range``) so the bars mean the same BPM range
# for every artist and a row's shape can be compared against its neighbours.
MIX_LO = 70.0
MIX_HI = 190.0
MIX_BINS = 16


def mix_bin(bpm: float) -> int:
    """The tempo-mix bin a BPM falls in, clamped to the first/last bin."""
    i = int((bpm - MIX_LO) / ((MIX_HI - MIX_LO) / MIX_BINS))
    return max(0, min(MIX_BINS - 1, i))


def bpm_in_window(bpm: float, target: float, tol: float, cadence: bool = False) -> bool:
    """Whether a track's BPM matches a tempo filter — the same rule the library
    listing applies in SQL (``_tracks_filter``): within ``target ± tol``, and with
    ``cadence`` also at half or double time (a 170 SPM cadence matches 85 BPM)."""
    lo, hi = target - tol, target + tol
    if lo <= bpm <= hi:
        return True
    return cadence and (lo / 2 <= bpm <= hi / 2 or lo * 2 <= bpm <= hi * 2)

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
