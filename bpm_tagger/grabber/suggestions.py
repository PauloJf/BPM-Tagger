"""SuggestionsEngine — recommend artists/tracks to grab from the library.

Owned by GrabberService. A refresh derives seed artists from what's already in
the library (starred tracks weigh heaviest), asks the keyless Deezer public
catalog for related artists + their top tracks, filters out anything you already
own or have dismissed, and stores the result. It runs on a one-off daemon thread
(guarded by a lock so only one runs at a time) — no always-on loop; the web
layer polls while ``refreshing`` is true.

All Deezer traffic goes through ``integrations/deezer_catalog`` (which uses the
shared ``deezer_limiter``); every network call is best-effort so a dead seed
skips rather than aborting the whole refresh.
"""

import logging
import re
import threading

from .matching import extract_feat, library_match, normalize_artist, normalize_title

log = logging.getLogger(__name__)

# Tuning (no env vars — these are engine internals).
SEED_LIMIT = 20         # top library artists used as recommendation seeds
ARTIST_LIMIT = 24       # suggested artists kept
TRACK_ARTISTS = 10      # how many top suggested artists get their tracks pulled
TRACKS_PER_ARTIST = 4   # top tracks pulled per artist
TTL_DAYS = 7            # staleness window before an auto-refresh
OWNED_THRESHOLD = 3     # >= this many library tracks by an artist = "you have them"


def primary_artist(artist: str, album_artist: str) -> str:
    """The one artist a track counts towards: album_artist when present, else the
    first name in the (feat-stripped) artist string split on , / & / /."""
    aa = (album_artist or "").strip()
    if aa:
        return aa
    base, _ = extract_feat(artist or "")
    for part in re.split(r"\s*[,&/]\s*", base):
        if part.strip():
            return part.strip()
    return (artist or "").strip()


def _library_artists_from_rows(rows: list[dict]) -> dict:
    """normalize_artist(primary) → (display_name, track_count) over the given
    (artist, album_artist, starred) rows."""
    counts: dict[str, tuple[str, int]] = {}
    for r in rows:
        p = primary_artist(r.get("artist"), r.get("album_artist"))
        if not p:
            continue
        key = normalize_artist(p)
        if not key:
            continue
        disp, n = counts.get(key, (p, 0))
        counts[key] = (disp, n + 1)
    return counts


def build_library_artists(db) -> dict:
    """Public helper (reused by the Related panel): normalize_artist(primary) →
    (display_name, track_count) over the whole library."""
    return _library_artists_from_rows(db.get_artist_index_rows())


class SuggestionsEngine:
    def __init__(self, config, db):
        self.config = config
        self.db = db
        self._lock = threading.Lock()
        self._refreshing = False
        self.last_error = ""

    @property
    def refreshing(self) -> bool:
        return self._refreshing

    def refresh_async(self) -> bool:
        """Kick a background refresh. Returns False if one is already running."""
        with self._lock:
            if self._refreshing:
                return False
            self._refreshing = True
        threading.Thread(target=self._run, name="SuggestionsRefresh", daemon=True).start()
        return True

    def _run(self):
        try:
            self.compute()
            self.last_error = ""
        except Exception as exc:  # never crash the thread
            log.exception("Suggestions refresh failed: %s", exc)
            self.last_error = str(exc)
        finally:
            self._refreshing = False

    # ── seed selection (pure DB, no network) ────────────────────────────────
    def _seeds_from_rows(self, rows: list[dict]) -> list[dict]:
        weights: dict[str, dict] = {}
        for r in rows:
            p = primary_artist(r.get("artist"), r.get("album_artist"))
            if not p:
                continue
            key = normalize_artist(p)
            if not key:
                continue
            w = weights.setdefault(key, {"name": p, "tracks": 0, "starred": 0})
            w["tracks"] += 1
            if r.get("starred"):
                w["starred"] += 1
        seeds = [{"key": k, "name": v["name"], "weight": v["tracks"] + 5 * v["starred"]}
                 for k, v in weights.items()]
        seeds.sort(key=lambda s: (-s["weight"], s["name"].lower()))
        return seeds[:SEED_LIMIT]

    # ── compute (network; runs inside the refresh thread) ───────────────────
    def compute(self) -> None:
        from ..integrations import deezer_catalog as dz

        rows = self.db.get_artist_index_rows()
        library_artists = _library_artists_from_rows(rows)
        seeds = self._seeds_from_rows(rows)
        dismissed_artists = self.db.get_dismissed_suggestion_keys("artist")
        dismissed_tracks = self.db.get_dismissed_suggestion_keys("track")

        total_weight = sum(s["weight"] for s in seeds) or 1

        # Aggregate related artists across seeds; score = Σ normalized seed weight.
        candidates: dict[str, dict] = {}
        for s in seeds:
            hit = dz.search_artist(s["name"])
            if not hit:
                continue
            for rel in dz.related_artists(hit["dz_id"]):
                key = normalize_artist(rel["name"])
                have = library_artists.get(key, ("", 0))[1]
                if have >= OWNED_THRESHOLD or key in dismissed_artists:
                    continue
                c = candidates.get(rel["dz_id"])
                if not c:
                    c = {"dz_id": rel["dz_id"], "name": rel["name"],
                         "image_url": rel["image_url"], "score": 0.0,
                         "seeds": [], "have_tracks": have}
                    candidates[rel["dz_id"]] = c
                c["score"] += s["weight"] / total_weight
                if len(c["seeds"]) < 3 and s["name"] not in c["seeds"]:
                    c["seeds"].append(s["name"])

        artist_rows = sorted(candidates.values(),
                             key=lambda c: -c["score"])[:ARTIST_LIMIT]

        # Track suggestions: top tracks of the strongest suggested artists,
        # minus anything the library already resolves or that's dismissed.
        track_rows: list[dict] = []
        seen: set[str] = set()
        for c in artist_rows[:TRACK_ARTISTS]:
            for t in dz.artist_top_tracks(c["dz_id"], TRACKS_PER_ARTIST):
                tid = t["dz_track_id"]
                if not tid or tid in dismissed_tracks or tid in seen:
                    continue
                meta = {"title": t["title"], "artist": t["artist"], "album": t["album"],
                        "duration_ms": t["duration_ms"], "isrc": "",
                        "norm_title": normalize_title(t["title"]),
                        "norm_artist": normalize_artist(t["artist"])}
                if library_match(meta, self.db):
                    continue
                seen.add(tid)
                track_rows.append({**t, "score": c["score"], "seeds": c["seeds"]})

        self.db.replace_suggestions(artist_rows, track_rows)
        log.info("Suggestions refreshed: %d artists, %d tracks from %d seeds",
                 len(artist_rows), len(track_rows), len(seeds))
