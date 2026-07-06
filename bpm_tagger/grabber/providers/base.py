"""Provider interface + shared data types (§5)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class TrackMeta:
    """The track we're searching for (from Spotify / the grab queue)."""
    title: str = ""
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    duration_ms: Optional[int] = None
    isrc: str = ""
    track_no: Optional[int] = None
    disc_no: Optional[int] = None
    year: Optional[int] = None

    @classmethod
    def from_row(cls, row: dict) -> "TrackMeta":
        return cls(
            title=row.get("title") or "",
            artist=row.get("artist") or "",
            album=row.get("album") or "",
            album_artist=row.get("album_artist") or "",
            duration_ms=row.get("duration_ms"),
            isrc=row.get("isrc") or "",
            track_no=row.get("track_no"),
            disc_no=row.get("disc_no"),
            year=row.get("year"),
        )

    def as_match(self) -> dict:
        """Shape expected by grabber.matching.score()."""
        return {"title": self.title, "artist": self.artist, "album": self.album,
                "duration_ms": self.duration_ms, "isrc": self.isrc}


@dataclass
class ProviderCandidate:
    provider: str
    provider_track_id: str
    title: str = ""
    artist: str = ""
    album: str = ""
    duration_ms: Optional[int] = None
    isrc: str = ""
    quality: str = ""
    url: str = ""
    cover_url: str = ""
    channel: str = ""          # yt-dlp uploader/channel (for the non-Topic penalty)
    is_topic: bool = True      # false → apply the yt non-"- Topic" channel penalty
    # Filled by the worker after scoring against the requested TrackMeta.
    score: float = 0.0
    score_breakdown: dict = field(default_factory=dict)
    rank: int = 0

    def as_match(self) -> dict:
        return {"title": self.title, "artist": self.artist, "album": self.album,
                "duration_ms": self.duration_ms, "isrc": self.isrc}


@dataclass
class DownloadedFile:
    path: str
    ext: str
    provider: str
    quality: str = ""


ProgressCb = Callable[[float], None]


class Provider(ABC):
    name: str = "base"
    lossless: bool = False

    @abstractmethod
    def search(self, meta: TrackMeta, limit: int = 8) -> list[ProviderCandidate]:
        ...

    @abstractmethod
    def download(self, cand: ProviderCandidate, dest_dir: str,
                 progress_cb: Optional[ProgressCb] = None) -> DownloadedFile:
        ...

    def healthcheck(self) -> bool:
        return True
