"""The composed BPMDatabase — track index, BPM results, playlists and accounts."""

from .base import _DBBase
from .players import PlayersMixin
from .playlists import PlaylistsMixin
from .ratings import RatingsMixin
from .runs import RunsMixin
from .subsonic import SubsonicMixin
from .tracks import TracksMixin


class BPMDatabase(TracksMixin, PlaylistsMixin, PlayersMixin, RunsMixin, SubsonicMixin,
                  RatingsMixin, _DBBase):
    """SQLite database (WAL). Behaviour is unchanged from the former single
    db.py module; the query surface is split into mixins by domain."""
