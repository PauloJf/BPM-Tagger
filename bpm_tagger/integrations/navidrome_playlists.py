"""Navidrome-sourced playlists (docs/plans/playlists-integration.md, Phase 2).

Mirrors the Spotify sync path, but the source is a Subsonic ``getPlaylist``. Each
playlist song is resolved to a local file the same way the grabber matches Spotify
tracks — by metadata via ``library_match`` — so the multi-root path fragility that
plain path matching would hit never gates coverage.

Independent of the grabber: needs only the Navidrome URL/credentials in config.
"""

import logging

from ..grabber.matching import library_match, normalize_artist, normalize_title
from .navidrome import get_playlist, get_playlists

log = logging.getLogger(__name__)


def _creds(config: dict) -> tuple[str, str, str]:
    return (str(config.get("navidrome_url", "")).rstrip("/"),
            str(config.get("navidrome_user", "")),
            str(config.get("navidrome_pass", "")))


def navidrome_configured(config: dict) -> bool:
    return all(_creds(config))


def list_navidrome_playlists(config: dict) -> list[dict]:
    """Importable Navidrome playlists (the configured user's own + public), as
    ``{navidrome_id, name, track_count, image_url}``."""
    url, user, pwd = _creds(config)
    out = []
    for p in get_playlists(url, user, pwd):
        pid = p.get("id")
        if pid is None:
            continue
        out.append({
            "navidrome_id": str(pid),
            "name": p.get("name") or "",
            "track_count": p.get("songCount") or 0,
            "image_url": p.get("coverArt") or "",
        })
    return out


def _song_to_track(song: dict, position: int) -> dict:
    """Adapt a Subsonic playlist entry to the dict sync_playlist_tracks() consumes
    (and library_match() scores). Subsonic duration is in seconds."""
    title = song.get("title") or ""
    artist = song.get("artist") or ""
    return {
        "source_track_id": str(song.get("id")) if song.get("id") is not None else None,
        "spotify_track_id": None,               # not a Spotify track
        "position": position,
        "title": title,
        "artist": artist,
        "album": song.get("album") or "",
        "album_artist": song.get("albumArtist") or artist,
        "duration_ms": (song.get("duration") or 0) * 1000,
        "isrc": None,
        "track_no": song.get("track"),
        "disc_no": song.get("discNumber"),
        "year": song.get("year"),
        "cover_url": song.get("coverArt") or "",
        "added_at": "",
        "norm_title": normalize_title(title),
        "norm_artist": normalize_artist(artist),
    }


def sync_navidrome_playlist(db, config: dict, playlist_id: int) -> dict:
    """Pull the Navidrome playlist, diff it into playlist_tracks, and classify each
    track have/missing against the local library. Returns the updated playlist row."""
    pl = db.get_playlist(playlist_id)
    if not pl or pl.get("source") != "navidrome" or not pl.get("navidrome_id"):
        raise ValueError("Not a Navidrome playlist")
    url, user, pwd = _creds(config)

    remote = get_playlist(url, user, pwd, pl["navidrome_id"])
    songs = remote.get("entry", []) or []
    tracks = [_song_to_track(s, i) for i, s in enumerate(songs)]
    added, removed = db.sync_playlist_tracks(playlist_id, tracks)

    # Classify current (non-tombstone) rows against the library. Re-run every sync
    # because the library changes even when the playlist doesn't.
    for row in db.get_playlist_track_rows(playlist_id):
        path = library_match(row, db)
        status = "have" if path else "missing"
        if status != row.get("match_status") or path != row.get("matched_file_path"):
            db.set_playlist_track_match(row["id"], status, path)

    db.mark_playlist_synced(playlist_id, name=remote.get("name") or pl.get("name"),
                            image_url=remote.get("coverArt") or pl.get("image_url"),
                            track_count=len(songs))
    log.info("Navidrome playlist '%s': %d tracks (+%d new, -%d removed)",
             pl.get("name"), len(songs), added, removed)
    return db.get_playlist(playlist_id)
