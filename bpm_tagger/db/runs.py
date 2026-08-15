"""Per-account play attribution and the run journal.

Three additive tables, all fed from the SAME recording paths the cumulative
Run-mode counters already use — nothing here is a parallel pipeline:

* ``runs``            — one row per run (a tempo-locked session), opened and
                        closed server-side from the run-stat events.
* ``play_events``     — one row per played track (the /api/scrobble halfway
                        report), attributed to an account and, mid-run, to a run.
* ``run_stats_owner`` — the cumulative ``run_stats`` counters mirrored per
                        account, so the Stats page can filter by owner.

The owner key is the convention ``player_state`` already uses — ``'admin'`` |
``'player:<id>'`` — extended with ``'guest'`` for the shared Guest login
(RUN_PASSWORD), which has no account row and therefore pools every Guest
device into one bucket.

**Run lifecycle (server-derived, no client-generated run id).** A run opens
lazily when a run-stat event arrives carrying run context (source + target) and
no open run for that owner applies. It closes when:

* the client reports the run ended (queue replaced by a non-run queue, tempo
  lock released, sign-out) — ``close_run()``;
* the next event for that owner arrives with a different source, or more than
  ``RUN_IDLE_SECONDS`` after the previous one (lazy close, so a phone that died
  mid-run doesn't leave a run "open" forever);

An open run reads as ended at its last event, so a run whose close never
arrived still shows an honest duration.

NO backfill: history starts at the first event recorded after the upgrade.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

# A run tolerates this long a gap between events before the next event is
# treated as a new run. Generous on purpose: a run is paused at traffic lights,
# and the client only flushes while audio actually plays.
RUN_IDLE_SECONDS = 30 * 60

# Cap a single event's contribution the same way add_run_stats caps a counter
# delta (24 h of ms), so one bad report can't invent a week-long run.
_MAX_EVENT_MS = 86_400_000.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse_stamp(value) -> Optional[datetime]:
    """Parse a stored ISO stamp, assuming UTC when it carries no offset."""
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _norm_source(raw) -> Optional[str]:
    """The run's source as a short opaque key: 'library' | 'mine' |
    'playlist:<id>'. Anything else (or missing) is stored as NULL."""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in ("library", "mine"):
        return s
    if s.startswith("playlist:"):
        tail = s.split(":", 1)[1]
        if tail.isdigit():
            return f"playlist:{int(tail)}"
    return None


def _norm_target(raw) -> Optional[float]:
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    return val if 30.0 <= val <= 300.0 else None


class RunsMixin:

    # ── Run journal ──────────────────────────────────────────────────────────
    def _apply_run_event(self, conn, owner: str, values: dict, run: dict,
                         now: Optional[datetime] = None) -> int:
        """Fold one run-stat batch into the owner's run row, opening or rolling
        over the run as the lifecycle rules above dictate. Returns the run id.

        Runs on the connection add_run_stats already opened — the journal write
        rides the same transaction as the counter write, so the two can never
        disagree about a batch."""
        now_dt = now or _now()
        source = _norm_source(run.get("source"))
        target = _norm_target(run.get("target"))
        wall = min(max(float(values.get("wall_ms") or 0.0), 0.0), _MAX_EVENT_MS)

        row = conn.execute(
            "SELECT id, source, last_event_at FROM runs "
            "WHERE owner = ? AND ended_at IS NULL ORDER BY id DESC LIMIT 1",
            (owner,)).fetchone()
        run_id = None
        if row is not None:
            last = parse_stamp(row["last_event_at"])
            stale = last is None or (now_dt - last).total_seconds() > RUN_IDLE_SECONDS
            if stale or (row["source"] or "") != (source or ""):
                # Lazy close: the run ended at its last event, not now.
                conn.execute("UPDATE runs SET ended_at = last_event_at WHERE id = ?",
                             (row["id"],))
            else:
                run_id = row["id"]

        if run_id is None:
            # The first event covers the wall time just elapsed, so back-date the
            # start by it — otherwise every run would lose its first flush window.
            started = now_dt - timedelta(milliseconds=wall)
            run_id = conn.execute(
                "INSERT INTO runs (owner, started_at, last_event_at, source, target_bpm) "
                "VALUES (?, ?, ?, ?, ?)",
                (owner, started.isoformat(), now_dt.isoformat(), source, target),
            ).lastrowid

        def _ms(key: str) -> float:
            return min(max(float(values.get(key) or 0.0), 0.0), _MAX_EVENT_MS)

        conn.execute(
            "UPDATE runs SET last_event_at = ?, target_bpm = COALESCE(?, target_bpm), "
            "tracks_played = tracks_played + ?, played_ms = played_ms + ?, "
            "stretched_ms = stretched_ms + ?, native_ms = native_ms + ?, "
            "cadence_ms_weighted = cadence_ms_weighted + ? WHERE id = ?",
            (now_dt.isoformat(), target, int(values.get("tracks_played") or 0), wall,
             _ms("shifted_ms"), _ms("native_ms"), _ms("cadence_weighted"), run_id))
        return run_id

    def close_run(self, owner: str) -> int:
        """End the owner's open run (if any), stamping it at its last event.
        Returns how many rows closed (0 or 1 in practice). Idempotent."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE runs SET ended_at = COALESCE(last_event_at, started_at) "
                "WHERE owner = ? AND ended_at IS NULL", (owner,))
            return cur.rowcount

    def get_open_run(self, owner: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE owner = ? AND ended_at IS NULL "
                "ORDER BY id DESC LIMIT 1", (owner,)).fetchone()
        return dict(row) if row else None

    def list_runs(self, limit: int = 15, offset: int = 0,
                  owner: Optional[str] = None) -> list[dict]:
        """Recent runs, newest first — the journal page. Optionally one owner's."""
        sql = "SELECT * FROM runs"
        params: list = []
        if owner:
            sql += " WHERE owner = ?"
            params.append(owner)
        sql += " ORDER BY started_at DESC, id DESC LIMIT ? OFFSET ?"
        params += [int(limit), max(0, int(offset))]
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ── Per-account play events ──────────────────────────────────────────────
    def record_play_event(self, owner: str, file_path: str, *,
                          duration_ms: Optional[int] = None,
                          cadence: Optional[float] = None,
                          stretched: bool = False,
                          in_run: bool = False,
                          now: Optional[datetime] = None) -> Optional[int]:
        """Attribute one play to an account (and to its open run when the client
        reports the play happened under a tempo lock).

        Additive to — never a replacement for — the library-global
        ``tracks.play_count`` the same request bumps."""
        now_dt = now or _now()
        with self._connect() as conn:
            run_id = None
            if in_run:
                row = conn.execute(
                    "SELECT id, last_event_at FROM runs WHERE owner = ? AND ended_at IS NULL "
                    "ORDER BY id DESC LIMIT 1", (owner,)).fetchone()
                if row is not None:
                    last = parse_stamp(row["last_event_at"])
                    if last is not None and (now_dt - last).total_seconds() <= RUN_IDLE_SECONDS:
                        run_id = row["id"]
            return conn.execute(
                "INSERT INTO play_events (owner, file_path, run_id, played_at, duration_ms, "
                "cadence, stretched) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (owner, file_path, run_id, now_dt.isoformat(), duration_ms, cadence,
                 int(bool(stretched)))).lastrowid

    def list_play_events(self, limit: int = 50, offset: int = 0,
                         owner: Optional[str] = None,
                         run_id: Optional[int] = None) -> list[dict]:
        sql = "SELECT * FROM play_events"
        where, params = [], []
        if owner:
            where.append("owner = ?")
            params.append(owner)
        if run_id is not None:
            where.append("run_id = ?")
            params.append(int(run_id))
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY played_at DESC, id DESC LIMIT ? OFFSET ?"
        params += [int(limit), max(0, int(offset))]
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ── Per-account cumulative counters ──────────────────────────────────────
    def get_run_stats_for_owner(self, owner: str) -> dict:
        """One account's cumulative run counters, same key shape as
        get_run_stats() (empty until that account runs)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT key, value FROM run_stats_owner WHERE owner = ?", (owner,)).fetchall()
        return {r["key"]: r["value"] for r in rows}

    def get_run_stats_attributed(self) -> dict:
        """The per-account counters summed over every owner. Subtracting this
        from get_run_stats() is the honest '(unattributed)' remainder — history
        recorded before per-account attribution existed."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT key, SUM(value) AS value FROM run_stats_owner GROUP BY key").fetchall()
        return {r["key"]: r["value"] for r in rows}

    def list_run_owners(self) -> list[str]:
        """Every owner key that has attributed run data (counters or journal)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT owner FROM run_stats_owner UNION SELECT owner FROM runs").fetchall()
        return sorted({r["owner"] for r in rows})
