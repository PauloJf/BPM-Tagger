"""Shared constants for the db package."""

# Grab-queue status state machine (§2). Non-terminal states mean the item is
# still "in flight"; exactly one non-terminal item may exist per spotify_track_id.
GRAB_TERMINAL = ("done", "failed", "skipped")
GRAB_NONTERMINAL = ("pending", "searching", "awaiting_user", "downloading",
                    "transcoding", "tagging", "analyzing_bpm")
