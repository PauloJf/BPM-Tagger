"""SQLite database package (split from the former monolithic db.py).

Public surface: import BPMDatabase and TRACK_SORTS straight from ``bpm_tagger.db``."""

from .constants import TRACK_SORTS
from .database import BPMDatabase

__all__ = ["BPMDatabase", "TRACK_SORTS"]
