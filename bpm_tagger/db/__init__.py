"""SQLite database package (split from the former monolithic db.py).

Public surface is unchanged: import BPMDatabase and the GRAB_* constants
straight from ``bpm_tagger.db`` exactly as before."""

from .constants import GRAB_NONTERMINAL, GRAB_TERMINAL, TRACK_SORTS
from .database import BPMDatabase

__all__ = ["BPMDatabase", "GRAB_TERMINAL", "GRAB_NONTERMINAL", "TRACK_SORTS"]
