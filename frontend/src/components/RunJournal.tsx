import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";

/** The run journal on the Stats page: one row per run, newest first.
 *
 *  A run is derived server-side from the usage events the player already posts
 *  while a tempo lock is held (see api/run.py) — the client never invents a run
 *  id, so a reloaded tab or a second device can't fork one run into two. Rows
 *  only exist for runs recorded since per-account attribution shipped; older
 *  listening lives on in the cumulative card above as "(unattributed)".
 *
 *  Paged like the Most played leaderboards: 15 at a time, Show more appends. */

export interface RunRow {
  id: number;
  owner: string;
  owner_label: string;
  started_at: string | null;
  ended_at: string | null;
  /** Still inside the idle window with no close reported — i.e. running now. */
  open: boolean;
  duration_ms: number;
  played_ms: number;
  source: string;
  source_label: string;
  target_bpm: number | null;
  tracks: number;
  avg_cadence: number | null;
  stretched_pct: number;
}

export interface RunJournalPage { items: RunRow[]; has_more: boolean }

/** Milliseconds → "1h 23m" / "12m" / "45s" — the Stats page's dense duration. */
export function fmtRunDur(ms: number): string {
  const s = Math.round((ms || 0) / 1000);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m`;
  return `${s}s`;
}

/** A run's start as "12 Mar, 07:40" — the date matters, the year rarely does. */
export function fmtRunWhen(iso: string | null): string {
  if (!iso) return "—";
  const t = new Date(iso);
  if (isNaN(t.getTime())) return "—";
  return `${t.toLocaleDateString(undefined, { day: "numeric", month: "short" })}, ` +
    t.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export default function RunJournal({ owner = "all" }: { owner?: string }) {
  const [items, setItems] = useState<RunRow[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  // Pre-attribution history was never recorded per run, so that bucket has no
  // journal to show — say so rather than rendering a misleading empty list.
  const unattributed = owner === "unattributed";
  const scope = owner && owner !== "all" && !unattributed
    ? `&owner=${encodeURIComponent(owner)}` : "";

  const load = useCallback(async (offset: number) => {
    const r = await api.get<RunJournalPage>(`/api/stats/runs?offset=${offset}${scope}`);
    setItems((prev) => (offset === 0 ? r.items : [...prev, ...r.items]));
    setHasMore(r.has_more);
  }, [scope]);

  useEffect(() => {
    if (unattributed) { setItems([]); setHasMore(false); setLoading(false); return; }
    let alive = true;
    setLoading(true);
    setFailed(false);
    load(0).catch(() => { if (alive) setFailed(true); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [load, unattributed]);

  async function more() {
    setLoading(true);
    try {
      await load(items.length);
    } catch { /* keep the button so the user can retry */ } finally {
      setLoading(false);
    }
  }

  return (
    <div className="card" style={{ marginTop: 18 }}>
      <div className="section-label">
        <span>Run journal</span>
        <span className="section-hint">every run since per-account attribution</span>
      </div>
      {unattributed ? (
        <p style={{ color: "var(--muted)", fontSize: 13, margin: 0 }}>
          Listening recorded before per-account attribution has no per-run detail — only the
          all-time totals above.
        </p>
      ) : failed ? (
        <p style={{ color: "var(--muted)", fontSize: 13, margin: 0 }}>Failed to load the run journal.</p>
      ) : items.length === 0 ? (
        <p style={{ color: "var(--muted)", fontSize: 13, margin: 0 }}>
          {loading ? "Loading runs…" : "No runs recorded yet. Start one from the Run page."}
        </p>
      ) : (
        <>
          <div className="run-journal">
            <table className="run-journal-table">
              <thead>
                <tr>
                  <th>When</th><th>Who</th><th>Duration</th><th>Source</th>
                  <th style={{ textAlign: "right" }}>Tracks</th>
                  <th style={{ textAlign: "right" }}>Cadence</th>
                  <th style={{ textAlign: "right" }}>Stretched</th>
                </tr>
              </thead>
              <tbody>
                {items.map((r) => (
                  <tr key={r.id}>
                    <td>
                      {fmtRunWhen(r.started_at)}
                      {r.open && <span style={{ color: "var(--accent-2)", marginLeft: 6 }}>· live</span>}
                    </td>
                    <td>{r.owner_label}</td>
                    <td className="num" style={{ textAlign: "left" }}>{fmtRunDur(r.duration_ms)}</td>
                    <td className="src" title={r.source_label}>{r.source_label}</td>
                    <td className="num">{r.tracks}</td>
                    <td className="num">{r.avg_cadence != null ? Math.round(r.avg_cadence) : "—"}</td>
                    <td className="num">{r.stretched_pct}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {hasMore && (
            <button className="btn btn-ghost btn-sm" style={{ width: "100%", marginTop: 8 }}
                    disabled={loading} onClick={more}>
              {loading ? "Loading…" : "Show more"}
            </button>
          )}
        </>
      )}
    </div>
  );
}
