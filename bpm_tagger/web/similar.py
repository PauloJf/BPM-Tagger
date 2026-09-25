"""Offline "similar tracks" from the library itself — no network, always playable.

One rule, shared by the web player (the Similar panel's "From your library"
section and Listen's similar radio) and the Subsonic API (getSimilarSongs):

1. tracks by the seed's own artists (each credited artist), in a rating-weighted
   random order, filling up to half the list;
2. then tracks at a nearby tempo, octave-folded like Run mode, so a 170 BPM seed
   also pulls 85 BPM tracks — a random over-fetch of the band, rating-weighted.

The tempo band is centred on the seed's BPM (median, for a multi-track seed) at
±5 %, or — while a run is tempo-locked — on the run's cadence at the run's
stretch limit, so everything offered can actually be played on cadence.

Ratings and dislikes are the ``owner``'s own (web/weighting.py): the account's
disliked tracks are never offered. ``scope`` is the usual playlist-id scope
(None = whole library) from ``db/subsonic.py``.
"""

import random
import statistics
from typing import Iterable, Optional

from ..text import normalize_artist_name, split_artist_credits
from .weighting import PickWeights, sample, weighted_order

SEED_TOLERANCE = 0.05
# How many tempo-band neighbours to draw from before weighting (a random slice
# of the band, so a huge band stays cheap).
BAND_POOL = 200


def _fits(bpm, target: float, tolerance: float) -> bool:
    """Octave-folded: can this track sit within ±tolerance of target?"""
    if not bpm:
        return False
    return any(abs(target / c - 1.0) <= tolerance + 1e-9 for c in (bpm, bpm / 2, bpm * 2))


def similar_tracks(db, seed: list, count: int, scope: Optional[list] = None,
                   exclude_paths: Iterable[str] = (), target: Optional[float] = None,
                   tolerance: Optional[float] = None, owner: str = "admin",
                   weights: Optional[PickWeights] = None,
                   rng: Optional[random.Random] = None) -> list[tuple[dict, str]]:
    """Up to ``count`` (track, reason) pairs, reason ``"artist"`` or ``"tempo"``.

    ``target`` / ``tolerance`` (a fraction) switch the tempo band from the
    seed's BPM to a run's cadence; with a target, same-artist picks must fit
    the cadence too. ``owner`` selects whose ratings/dislikes apply;
    ``weights`` None = flat (dislikes still excluded)."""
    if count <= 0 or not seed:
        return []
    weights = weights or PickWeights(use_ratings=False)
    rng = rng or random.Random()
    excluded = set(exclude_paths) | {t["file_path"] for t in seed}
    tol = SEED_TOLERANCE if tolerance is None else tolerance
    picked: list[tuple[dict, str]] = []

    def take(t: dict, reason: str) -> None:
        if t["file_path"] in excluded or t.get("disliked"):
            return
        if target is not None and not _fits(t.get("bpm"), target, tol):
            return
        excluded.add(t["file_path"])
        picked.append((t, reason))

    norms = {normalize_artist_name(c) for t in seed for c in split_artist_credits(t.get("artist"))}
    same = [t for n in sorted(norms) for t in db.subsonic_artist_tracks(n, scope)]
    same = weighted_order(db.annotate_marks(same, owner), weights.weight, rng)
    half = max(1, count // 2)
    for t in same:
        if len(picked) >= half:
            break
        take(t, "artist")

    if target is not None:
        centre = target
    else:
        bpms = [t["bpm"] for t in seed if t.get("bpm")]
        centre = statistics.median(bpms) if bpms else None
    if centre and len(picked) < count:
        # Over-fetch: some neighbours may be excluded (recently queued, disliked).
        want = count - len(picked)
        band = db.annotate_marks(
            db.subsonic_bpm_neighbours(centre, tol, [], max(BAND_POOL, want + len(excluded) + 20),
                                       scope), owner)
        for t in sample(band, len(band), weights.weight, rng):
            if len(picked) >= count:
                break
            take(t, "tempo")
    # Top up from the artist pool if the tempo band ran dry.
    for t in same:
        if len(picked) >= count:
            break
        take(t, "artist")
    return picked[:count]
