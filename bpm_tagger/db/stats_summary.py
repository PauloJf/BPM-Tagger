"""Read-only aggregates behind ``GET /api/stats/summary`` (a dashboard feed; see web/api/stats_summary.py).

Library sizes come from ``file_hash`` (``size:mtime``, written by the scanner), so no file is touched. Listening
numbers come from ``play_events`` (one row per played track), joined to ``tracks`` for the artist and as the
fallback length. Windows are UTC instants (``window_starts``); stored stamps are UTC ISO strings, so they compare as text.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    from zoneinfo import ZoneInfo
    STATS_TZ = ZoneInfo("Europe/Lisbon")
except Exception:                       # no tz database installed (the image ships tzdata): UTC windows
    STATS_TZ = timezone.utc

WINDOWS = ("week", "month", "year", "all")

# Played length: what the player reported, else the track's own length, else nothing.
_PLAYED_MS = "COALESCE(pe.duration_ms, t.duration_ms, 0)"
_ARTIST = "COALESCE(NULLIF(t.norm_artist, ''), NULLIF(LOWER(t.artist), ''))"
_PLAYS_FROM = "FROM play_events pe LEFT JOIN tracks t ON t.file_path = pe.file_path"


def window_starts(now: Optional[datetime] = None, tz=STATS_TZ) -> dict:
    """UTC instants at which the current calendar week (Monday), month and year began in ``tz``."""
    local = (now or datetime.now(timezone.utc)).astimezone(tz)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    starts = {"week": midnight - timedelta(days=local.weekday()),
              "month": midnight.replace(day=1),
              "year": midnight.replace(month=1, day=1)}
    return {k: v.astimezone(timezone.utc) for k, v in starts.items()}


class StatsSummaryMixin:
    def stats_library_totals(self) -> dict:
        """Tracks, artists, albums, size and length of the library (deleted rows excluded)."""
        with self._connect() as conn:
            row = conn.execute("""
                SELECT COUNT(*) AS tracks,
                       COUNT(DISTINCT NULLIF(norm_artist, '')) AS artists,
                       COUNT(DISTINCT CASE WHEN album IS NOT NULL AND album <> ''
                             THEN LOWER(COALESCE(NULLIF(album_artist, ''), artist, '')) || char(31) || LOWER(album) END) AS albums,
                       COALESCE(SUM(CASE WHEN file_hash GLOB '[0-9]*:*'
                             THEN CAST(substr(file_hash, 1, instr(file_hash, ':') - 1) AS INTEGER) END), 0) AS size_bytes,
                       COALESCE(SUM(duration_ms), 0) AS duration_ms
                FROM tracks WHERE status != 'deleted'
            """).fetchone()
        return dict(row)

    def stats_tagging_state(self) -> dict:
        with self._connect() as conn:
            row = conn.execute("""
                SELECT COUNT(CASE WHEN status='done'    THEN 1 END) AS analysed,
                       COUNT(CASE WHEN status='pending' THEN 1 END) AS pending,
                       COUNT(CASE WHEN status='error'   THEN 1 END) AS errors,
                       COUNT(CASE WHEN needs_review=1 AND status='done' AND locked=0 AND reviewed=0 THEN 1 END) AS needs_review
                FROM tracks
            """).fetchone()
        return dict(row)

    def stats_listening(self, starts: dict, owner: Optional[str] = None) -> dict:
        """{window: {"plays", "minutes", "artists"}} for week/month/year/all. ``owner`` limits it to one account."""
        out = {}
        with self._connect() as conn:
            for w in WINDOWS:
                conds, params = [], []
                if owner:
                    conds.append("pe.owner = ?")
                    params.append(owner)
                if starts.get(w):
                    conds.append("pe.played_at >= ?")
                    params.append(starts[w])
                where = ("WHERE " + " AND ".join(conds)) if conds else ""
                row = conn.execute(
                    f"SELECT COUNT(*) AS plays, COALESCE(SUM({_PLAYED_MS}), 0) AS ms, COUNT(DISTINCT {_ARTIST}) AS artists "
                    f"{_PLAYS_FROM} {where}", params).fetchone()
                out[w] = {"plays": row["plays"], "minutes": row["ms"] / 60000.0, "artists": row["artists"]}
        return out

    def stats_listening_by_owner(self, starts: dict) -> dict:
        """{owner: {"name", "plays_<w>", "minutes_<w>", "artists_<w>"}} for every account with a play."""
        with self._connect() as conn:
            owners = [r[0] for r in conn.execute("SELECT DISTINCT owner FROM play_events ORDER BY owner")]
            names = {f"player:{r['id']}": r["username"] for r in conn.execute("SELECT id, username FROM players")}
        out = {}
        for o in owners:
            row = {"name": names.get(o, o)}
            for w, d in self.stats_listening(starts, o).items():
                row.update({f"plays_{w}": d["plays"], f"minutes_{w}": round(d["minutes"], 1), f"artists_{w}": d["artists"]})
            out[o] = row
        return out

    def stats_top_since(self, since: Optional[str], owner: Optional[str] = None) -> dict:
        """The most-played artist and track since ``since`` (UTC ISO). Ties go to the longer listening, then the name."""
        conds, params = [], []
        if owner:
            conds.append("pe.owner = ?")
            params.append(owner)
        if since:
            conds.append("pe.played_at >= ?")
            params.append(since)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        with self._connect() as conn:
            artist = conn.execute(
                f"SELECT MIN(t.artist) AS name, COUNT(*) AS plays {_PLAYS_FROM} {where}"
                f"{' AND ' if where else 'WHERE '}{_ARTIST} IS NOT NULL "
                f"GROUP BY {_ARTIST} ORDER BY plays DESC, SUM({_PLAYED_MS}) DESC, name LIMIT 1", params).fetchone()
            track = conn.execute(
                f"SELECT pe.file_path AS path, MIN(t.title) AS title, MIN(t.artist) AS artist, COUNT(*) AS plays "
                f"{_PLAYS_FROM} {where} GROUP BY pe.file_path "
                f"ORDER BY plays DESC, SUM({_PLAYED_MS}) DESC, pe.file_path LIMIT 1", params).fetchone()
        return {"artist": dict(artist) if artist else None, "track": dict(track) if track else None}
