import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import BpmHistogram, { type BpmBucket } from "./BpmHistogram";

/** Rollup over a playlist's MATCHED (library-backed) tracks — the numbers the
 *  coverage chips above it can't answer: how long it actually is, what shape its
 *  tempos are, how much you've played it, and how much of it you could run.
 *
 *  Its own fetch (`/api/playlists/<id>/stats`, cached per playlist) rather than
 *  fields on the track listing: the listing is refetched on every tab switch and
 *  these numbers are the same on all of them. Admin-only server-side, so a
 *  player session simply gets nothing — a failure renders no strip rather than
 *  an error, since this complements the page instead of being it. */

export interface PlaylistStatsResponse {
  matched: {
    count: number;
    runtime_ms: number;
    analyzed: number;
    bpm_distribution: BpmBucket[];
    plays_total: number;
    top_played: Array<{ path: string; title: string | null; artist: string | null; play_count: number }>;
  };
  presets: Array<{ name: string; bpm: number }>;
  /** Preset BPM (as a string key) → runnable matched tracks. Same numbers as the
   *  playlist cards' badges — both come from the backend's preset_counts(). */
  runnable: Record<string, number>;
  stretch_limit_pct: number;
  octave_fold: boolean;
  source: string;
  last_synced_at: string | null;
  /** Latest membership change the schema can attest to (a row appearing, or a
   *  sync tombstone). Null on a playlist whose rows predate both stamps. */
  last_change_at: string | null;
}

/** Milliseconds → "3 h 12 m" / "12 m" / "—". Spaced units to match the strip's
 *  other quiet metadata; the Stats page's tighter "1h 23m" belongs to its own
 *  denser grid. */
export function fmtRuntime(ms: number): string {
  const mins = Math.round((ms || 0) / 60000);
  if (mins <= 0) return "—";
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return h ? `${h} h ${m} m` : `${m} m`;
}

/** An ISO timestamp as a coarse "today / 3 days ago / 2 Mar 2026" age. Coarse on
 *  purpose: a playlist changing is a slow event, and "17 days ago" says more
 *  than a wall-clock time nobody can place. */
export function fmtAge(iso: string | null): string {
  if (!iso) return "unknown";
  const t = new Date(iso).getTime();
  if (isNaN(t)) return "unknown";
  const days = Math.floor((Date.now() - t) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 30) return `${days} days ago`;
  return new Date(t).toLocaleDateString();
}

function Metric({ label, value, hint, title }: {
  label: string; value: string; hint?: React.ReactNode; title?: string;
}) {
  return (
    <div title={title}>
      <div className="stat-label">{label}</div>
      <div style={{ fontFamily: "var(--mono)", fontSize: 20, fontWeight: 600, color: "var(--text)", letterSpacing: "-0.02em", fontVariantNumeric: "tabular-nums" }}>
        {value}
      </div>
      {hint && <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>{hint}</div>}
    </div>
  );
}

export default function PlaylistStats({ playlistId }: { playlistId: string }) {
  const statsQ = useQuery({
    queryKey: ["playlist-stats", playlistId],
    queryFn: () => api.get<PlaylistStatsResponse>(`/api/playlists/${playlistId}/stats`),
    enabled: !!playlistId,
    staleTime: 60_000,
    retry: false,
  });

  const s = statsQ.data;
  // Nothing matched yet → the strip would be six em-dashes; the coverage chips
  // already say "0 have", which is the honest version of that.
  if (!s || s.matched.count === 0) return null;

  const { matched } = s;
  const runnable = s.presets.filter((p) => (s.runnable[String(p.bpm)] ?? 0) > 0);
  const stale = s.source === "local" ? s.last_change_at : (s.last_change_at || s.last_synced_at);
  const staleLabel = s.source === "local" ? "Last changed" : "Last change";

  return (
    <div className="card" style={{ marginBottom: 16 }} data-testid="playlist-stats">
      <div className="section-label">
        <span>In your library</span>
        <span className="section-hint">
          across the {matched.count} matched track{matched.count === 1 ? "" : "s"}
        </span>
      </div>

      <div className="desc-grid" style={{ marginBottom: 14 }}>
        <Metric
          label="Runtime"
          value={fmtRuntime(matched.runtime_ms)}
          hint={`${matched.count} track${matched.count === 1 ? "" : "s"} on disk`}
          title="Total length of this playlist's tracks that are in your library"
        />
        <Metric
          label="Plays"
          value={matched.plays_total.toLocaleString()}
          hint={matched.top_played.length ? (
            <span style={{ display: "block" }}>
              {matched.top_played.map((t, i) => (
                <span key={t.path} style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  <Link to={`/track?path=${encodeURIComponent(t.path)}`} style={{ color: "inherit", textDecoration: "none" }} title={t.artist || undefined}>
                    {i + 1}. {t.title || t.path} · {t.play_count}
                  </Link>
                </span>
              ))}
            </span>
          ) : "never played"}
          title="Total plays across the matched tracks, and the three most played"
        />
        <Metric
          label="Runnable"
          value={runnable.length ? String(Math.max(...runnable.map((p) => s.runnable[String(p.bpm)] ?? 0))) : "—"}
          hint={runnable.length ? (
            <span style={{ display: "flex", gap: 6, flexWrap: "wrap", fontFamily: "var(--mono)" }}>
              {runnable.map((p) => (
                <span key={p.bpm} title={`${s.runnable[String(p.bpm)]} tracks runnable at ${p.name} (${p.bpm} BPM) within ±${s.stretch_limit_pct.toFixed(1)}%`}>
                  {p.bpm}:{s.runnable[String(p.bpm)]}
                </span>
              ))}
            </span>
          ) : "no cadence match"}
          title="How many matched tracks can be pulled onto each run preset"
        />
        <Metric
          label={staleLabel}
          value={fmtAge(stale)}
          hint={s.source === "local" ? "membership" : `synced ${fmtAge(s.last_synced_at)}`}
          title={stale || "No timestamped change on record"}
        />
      </div>

      <div className="section-label" style={{ marginBottom: 6 }}>
        <span>Tempo spread</span>
        <span className="section-hint">
          {matched.analyzed} of {matched.count} analyzed
        </span>
      </div>
      <BpmHistogram dist={matched.bpm_distribution} mini emptyText="No BPMs yet." />
    </div>
  );
}
