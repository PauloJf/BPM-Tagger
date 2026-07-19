"""Source-agnostic grab enqueue (Phase 5, docs/plans/phase5-player-users.md §8).

`enqueue_grab` (db.py) dedupes on ``spotify_track_id``. A track from a non-Spotify
source — a missing Navidrome playlist track, a Deezer suggestion — carries no
Spotify id, so we need to enrich it before queuing:

  1. When Spotify is connected, adopt a confident (>=0.9) search match's
     spotify_track_id + ISRC (+ album_artist / track_no / disc_no / year) → gives
     full sid-based dedupe and better provider matching.
  2. Else, if a Deezer track id is known, fall back to its ISRC.
  3. Hand off to ``enqueue_grab``, which dedupes on the sid when present and on a
     normalized (title, artist) key otherwise.

This is the logic ``suggestions.py::queue_suggestion`` grew first; extracting it here
lets the "queue missing in grabber" playlist action reuse it verbatim rather than
inventing a second mechanism. The adopted sid lives only on the grab_queue item — we
deliberately do NOT write it back onto the playlist_tracks row (the next sync would
null it; see the design doc §8).
"""

import logging
from typing import Optional

from .matching import score

log = logging.getLogger(__name__)


def enqueue_track(db, grabber, meta: dict, *, playlist_track_id: Optional[int] = None,
                  dz_track_id: str = "") -> Optional[int]:
    """Enrich + enqueue one track. ``meta`` needs at least title/artist (album,
    album_artist, duration_ms, cover_url, isrc are used when present). ``grabber`` may
    be None (skips Spotify adoption). Returns the new queue-item id, or None if the
    track was already queued (deduped)."""
    meta = dict(meta)
    if playlist_track_id is not None:
        meta["playlist_track_id"] = playlist_track_id
    meta.setdefault("isrc", "")
    meta.setdefault("album_artist", meta.get("artist") or "")

    # A track that already carries a Spotify id (a Spotify-sourced playlist row) needs
    # no search-and-adopt — it's already dedupe-ready. Only non-Spotify tracks search.
    adopted = bool(meta.get("spotify_track_id"))
    client = getattr(grabber, "client", None) if grabber else None
    try:
        if not adopted and client is not None and client.is_connected() \
                and (meta.get("artist") or meta.get("title")):
            query = f"{meta.get('artist') or ''} {meta.get('title') or ''}".strip()
            best, best_s = None, 0.0
            for r in client.search_tracks(query, limit=5):
                s, _ = score(meta, r)
                if s > best_s:
                    best_s, best = s, r
            if best and best_s >= 0.9:
                meta["spotify_track_id"] = best.get("spotify_track_id")
                meta["isrc"] = best.get("isrc") or ""
                meta["album_artist"] = best.get("album_artist") or meta["album_artist"]
                meta["track_no"] = best.get("track_no")
                meta["disc_no"] = best.get("disc_no")
                meta["year"] = best.get("year")
                adopted = True
    except Exception as exc:  # never fail the enqueue on a best-effort lookup
        log.debug("Spotify enrichment failed for enqueue: %s", exc)

    if not adopted and dz_track_id and not meta.get("isrc"):
        try:
            from ..integrations import deezer_catalog as dz
            meta["isrc"] = dz.track_isrc(dz_track_id)
        except Exception as exc:
            log.debug("Deezer ISRC lookup failed for %s: %s", dz_track_id, exc)

    return db.enqueue_grab(meta)
