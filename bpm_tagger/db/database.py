"""The composed BPMDatabase — track index, BPM results, and grabber state."""

from .base import _DBBase
from .grabber import GrabberMixin
from .players import PlayersMixin
from .playlists import PlaylistsMixin
from .suggestions import SuggestionsMixin
from .tracks import TracksMixin


class BPMDatabase(TracksMixin, GrabberMixin, PlaylistsMixin,
                  PlayersMixin, SuggestionsMixin, _DBBase):
    """SQLite database (WAL). Behaviour is unchanged from the former single
    db.py module; the query surface is split into mixins by domain."""
