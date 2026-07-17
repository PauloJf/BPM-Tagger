import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useTitle } from "../hooks/useTitle";
import PageHeader from "../components/PageHeader";

interface StatsResponse {
  summary: {
    total: number;
    done: number;
    needs_review: number;
    errors: number;
    locked: number;
    reviewed: number;
    pending: number;
    deleted: number;
    missing_isrc: number;
  };
  bpm_descriptive: { avg: number | null; median: number | null; min: number | null; max: number | null };
  bpm_distribution: { bpm: number; count: number }[];
  detector_distribution: { detector: string; count: number }[];
  // Cumulative run-mode usage counters (empty before the first run). Fixed keys
  // (wall_ms, shifted_ms, native_ms, cadence_weighted, tracks_played) plus
  // dynamic per-cadence-bin buckets keyed cad_<bpm> (10-BPM wide).
  run?: Record<string, number>;
  // Present only when the grabber is enabled.
  grabber?: {
    managed: number;
    unmanaged: number;
    providers: { provider: string; count: number }[];
    queue: Record<string, number>;
    duplicate_groups: number;
    duplicate_tracks: number;
    playlists: { total: number; watched: number; have: number; missing: number; queued: number };
  };
}

const fmt = (v: number | null, dec = 0) => (v == null ? "—" : Number(v).toFixed(dec));
const num = (v: number | undefined) => (v || 0).toLocaleString();

/** Milliseconds → a compact "1h 23m" / "12m 30s" / "45s" duration. */
function fmtDur(ms: number): string {
  const s = Math.round((ms || 0) / 1000);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${sec}s`;
  return `${sec}s`;
}

const bigNum: React.CSSProperties = {
  fontFamily: "var(--mono)", fontSize: 22, fontWeight: 600, color: "var(--text)",
  letterSpacing: "-0.02em", fontVariantNumeric: "tabular-nums",
};

function StatCard({ label, value, color, children }: { label: string; value: string; color: string; children?: React.ReactNode }) {
  return (
    <div className="stat-card">
      <div className="stat-label">{label}</div>
      <div className="stat-value" style={{ color }}>
        {value}
      </div>
      {children}
    </div>
  );
}

export default function Stats() {
  useTitle("Statistics");
  const navigate = useNavigate();
  const qc = useQueryClient();
  const statsQ = useQuery({ queryKey: ["stats"], queryFn: () => api.get<StatsResponse>("/api/stats") });

  const retryErrors = useMutation({
    mutationFn: () => api.post<{ ok: boolean }>("/api/scan/retry_errors", {}),
    onSuccess: (d) => {
      if (d.ok) {
        qc.invalidateQueries({ queryKey: ["tracks"] });
        navigate("/tracks");
      }
    },
  });

  if (statsQ.isLoading) return <div style={{ color: "var(--muted)", padding: "40px 0", textAlign: "center", fontSize: 13 }}>Loading statistics…</div>;
  if (statsQ.isError || !statsQ.data) return <div style={{ color: "var(--muted)", padding: "40px 0", textAlign: "center" }}>Failed to load statistics.</div>;

  const { summary: s, bpm_descriptive: d, bpm_distribution: dist, detector_distribution: dets } = statsQ.data;

  const maxCount = dist.length ? Math.max(1, ...dist.map((x) => x.count)) : 1;
  const peakBpm = dist.length ? dist.reduce((a, x) => (x.count > a.count ? x : a), dist[0]).bpm : null;
  const bpmMin = dist.length ? dist[0].bpm : 0;
  const bpmMax = dist.length ? dist[dist.length - 1].bpm + 5 : 1;
  const medianPct = d.median != null ? Math.max(0, Math.min(100, ((d.median - bpmMin) / (bpmMax - bpmMin)) * 100)) : null;
  const totalDet = dets.reduce((a, x) => a + x.count, 0) || 1;

  return (
    <>
      <PageHeader title="Statistics" subtitle="Library health, BPM distribution, and detector performance." />

      <div className="stat-grid">
        <StatCard label="Total" value={num(s.total)} color="var(--text)" />
        <StatCard label="Analyzed" value={num(s.done)} color="var(--ok-fg)" />
        <StatCard label="Review" value={num(s.needs_review)} color="var(--warn-fg)" />
        <StatCard label="Errors" value={num(s.errors)} color="var(--err-fg)">
          {s.errors > 0 && (
            <button className="btn btn-ghost btn-sm" style={{ marginTop: 10, width: "100%", fontSize: 11 }} disabled={retryErrors.isPending} onClick={() => retryErrors.mutate()}>
              {retryErrors.isPending ? "Starting…" : "Retry →"}
            </button>
          )}
        </StatCard>
        <StatCard label="Locked" value={num(s.locked)} color="var(--info-fg)" />
        <div className="stat-card">
          <div className="stat-label">Reviewed</div>
          <div className="stat-value" style={{ color: "var(--ok-fg)", opacity: 0.7 }}>{num(s.reviewed)}</div>
        </div>
        <StatCard label="Pending" value={num(s.pending)} color="var(--muted)" />
        <StatCard label="Deleted" value={num(s.deleted)} color="var(--muted)" />
      </div>

      <div className="card" style={{ marginBottom: 18 }}>
        <div className="section-label">
          <span>BPM distribution</span>
          <span className="section-hint">across {num(s.done)} analyzed tracks</span>
        </div>
        <div className="desc-grid">
          {[
            { label: "Mean", val: d.avg },
            { label: "Median", val: d.median },
            { label: "Min", val: d.min },
            { label: "Max", val: d.max },
          ].map((x) => (
            <div key={x.label}>
              <div className="stat-label">{x.label}</div>
              <div style={{ fontFamily: "var(--mono)", fontSize: 22, fontWeight: 600, color: "var(--text)", letterSpacing: "-0.02em", fontVariantNumeric: "tabular-nums" }}>{fmt(x.val, 1)}</div>
            </div>
          ))}
        </div>
        <div className="histogram-wrap">
          <div className="hist-gridline" style={{ bottom: "75%" }} />
          <div className="hist-gridline" style={{ bottom: "50%" }} />
          <div className="hist-gridline" style={{ bottom: "25%" }} />
          {dist.length ? (
            <>
              {dist.map((dd, i) => (
                <div
                  key={i}
                  className="hist-bar"
                  title={`${dd.bpm}–${dd.bpm + 5} BPM · ${dd.count}${dd.bpm === peakBpm ? " (peak)" : ""}`}
                  style={{
                    height: `${(dd.count / maxCount) * 100}%`,
                    background: dd.bpm === peakBpm ? "var(--accent-2)" : dd.bpm >= 100 && dd.bpm <= 160 ? "var(--accent)" : "var(--accent-soft-strong)",
                  }}
                />
              ))}
              {medianPct != null && (
                <div title={`Median: ${fmt(d.median, 1)} BPM`} style={{ position: "absolute", top: 0, bottom: 0, width: 2, background: "var(--warn-fg)", opacity: 0.8, left: `${medianPct}%`, pointerEvents: "none", borderRadius: 1 }} />
              )}
            </>
          ) : (
            <p style={{ color: "var(--muted)", fontSize: 13, alignSelf: "center", padding: 20 }}>No data yet.</p>
          )}
        </div>
        <div className="hist-x-labels">
          <span>60</span>
          <span>90</span>
          <span>120</span>
          <span>150</span>
          <span>180</span>
          <span>200</span>
        </div>
      </div>

      <div className="card">
        <div className="section-label">
          <span>Detector breakdown</span>
          <span className="section-hint">how often each detector combo was used</span>
        </div>
        <div>
          {dets.length ? (
            dets.map((dd, i) => {
              const pct = (dd.count / totalDet) * 100;
              const isPrimary = dd.detector === "deeprhythm+essentia";
              return (
                <div className="det-row" key={i}>
                  <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={dd.detector}>
                    {dd.detector}
                  </span>
                  <div className="det-bar-track">
                    <div className="det-bar-fill" style={{ width: `${pct.toFixed(1)}%`, background: isPrimary ? "linear-gradient(90deg,var(--accent),var(--accent-2))" : "var(--accent-soft-strong)" }} />
                  </div>
                  <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--muted)", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                    {dd.count.toLocaleString()} · {pct.toFixed(1)}%
                  </span>
                </div>
              );
            })
          ) : (
            <p style={{ color: "var(--muted)", fontSize: 13 }}>No data yet.</p>
          )}
        </div>
      </div>

      {(() => {
        const run = statsQ.data.run || {};
        const wall = run.wall_ms || 0;
        const tracks = run.tracks_played || 0;
        if (!wall && !tracks) return null;   // nothing recorded yet → hide the card
        const shifted = run.shifted_ms || 0;
        const unshifted = Math.max(0, wall - shifted);
        const nativeMs = run.native_ms || 0;
        const avgCad = wall > 0 ? (run.cadence_weighted || 0) / wall : null;
        const shiftedPct = wall > 0 ? Math.round((shifted / wall) * 100) : 0;
        const bins = Object.entries(run)
          .filter(([k]) => k.startsWith("cad_"))
          .map(([k, v]) => ({ bpm: Number(k.slice(4)), ms: v }))
          .filter((b) => Number.isFinite(b.bpm) && b.ms > 0)
          .sort((a, b) => a.bpm - b.bpm);
        const maxBin = bins.length ? Math.max(...bins.map((b) => b.ms)) : 1;
        return (
          <div className="card" style={{ marginTop: 18 }}>
            <div className="section-label">
              <span>Run mode</span>
              <span className="section-hint">tempo-locked listening, all-time</span>
            </div>
            <div className="desc-grid">
              <div>
                <div className="stat-label">Tracks played</div>
                <div style={{ ...bigNum, color: "var(--accent-2)" }}>{num(tracks)}</div>
              </div>
              <div>
                <div className="stat-label">Time on feet</div>
                <div style={bigNum}>{fmtDur(wall)}</div>
                <div style={{ fontSize: 11, color: "var(--muted)" }}>{fmtDur(nativeMs)} of music covered</div>
              </div>
              <div>
                <div className="stat-label">Tempo-shifted</div>
                <div style={bigNum}>{shiftedPct}%</div>
                <div style={{ fontSize: 11, color: "var(--muted)" }}>{fmtDur(unshifted)} at native speed</div>
              </div>
              <div>
                <div className="stat-label">Avg cadence</div>
                <div style={bigNum}>
                  {avgCad != null ? Math.round(avgCad) : "—"}
                  <span style={{ fontSize: 12, color: "var(--muted)", marginLeft: 4 }}>BPM</span>
                </div>
              </div>
            </div>
            {bins.length > 0 && (
              <div>
                <div style={{ fontSize: 11, color: "var(--muted)", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 8 }}>
                  Time per cadence
                </div>
                {bins.map((b) => {
                  const pct = (b.ms / maxBin) * 100;
                  return (
                    <div className="det-row" key={b.bpm}>
                      <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--text)", whiteSpace: "nowrap" }}>
                        {b.bpm}–{b.bpm + 9}
                      </span>
                      <div className="det-bar-track">
                        <div className="det-bar-fill" style={{ width: `${pct.toFixed(1)}%`, background: "linear-gradient(90deg,var(--accent),var(--accent-2))" }} />
                      </div>
                      <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--muted)", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                        {fmtDur(b.ms)}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })()}

      {statsQ.data.grabber && (() => {
        const g = statsQ.data.grabber;
        const libTotal = g.managed + g.unmanaged || 1;
        const dlTotal = g.providers.reduce((a, p) => a + p.count, 0) || 1;
        const failed = g.queue.failed ?? 0;
        const inbox = g.queue.awaiting_user ?? 0;
        return (
          <div className="card" style={{ marginTop: 18 }}>
            <div className="section-label">
              <span>Library sources</span>
              <span className="section-hint">where your tracks came from (grabber)</span>
            </div>

            <div className="desc-grid">
              <div>
                <div className="stat-label">Grabbed</div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 22, fontWeight: 600, color: "var(--accent-2)", letterSpacing: "-0.02em", fontVariantNumeric: "tabular-nums" }}>
                  {num(g.managed)}
                </div>
                <div style={{ fontSize: 11, color: "var(--muted)" }}>{Math.round((g.managed / libTotal) * 100)}% of library</div>
              </div>
              <div>
                <div className="stat-label">Pre-existing</div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 22, fontWeight: 600, color: "var(--text)", letterSpacing: "-0.02em", fontVariantNumeric: "tabular-nums" }}>
                  {num(g.unmanaged)}
                </div>
                <div style={{ fontSize: 11, color: "var(--muted)" }}>already on disk</div>
              </div>
              <div>
                <div className="stat-label">Duplicates</div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 22, fontWeight: 600, color: g.duplicate_groups ? "var(--warn-fg)" : "var(--ok-fg)", letterSpacing: "-0.02em", fontVariantNumeric: "tabular-nums" }}>
                  {num(g.duplicate_tracks)}
                </div>
                <div style={{ fontSize: 11 }}>
                  {g.duplicate_groups ? (
                    <Link to="/duplicates" style={{ color: "var(--accent-2)" }}>{g.duplicate_groups} group{g.duplicate_groups === 1 ? "" : "s"} → resolve</Link>
                  ) : (
                    <span style={{ color: "var(--muted)" }}>none found</span>
                  )}
                </div>
              </div>
              <div>
                <div className="stat-label">Missing ISRC</div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 22, fontWeight: 600, color: "var(--text)", letterSpacing: "-0.02em", fontVariantNumeric: "tabular-nums" }}>
                  {num(s.missing_isrc)}
                </div>
                <div style={{ fontSize: 11 }}>
                  {s.missing_isrc > 0 ? (
                    <Link to="/settings" style={{ color: "var(--accent-2)" }}>bulk fill in Settings →</Link>
                  ) : (
                    <span style={{ color: "var(--muted)" }}>all tagged</span>
                  )}
                </div>
              </div>
            </div>

            {g.providers.length > 0 && (
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 11, color: "var(--muted)", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 8 }}>
                  Downloads by provider
                </div>
                {g.providers.map((p) => {
                  const pct = (p.count / dlTotal) * 100;
                  return (
                    <div className="det-row" key={p.provider}>
                      <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={p.provider}>
                        {p.provider}
                      </span>
                      <div className="det-bar-track">
                        <div className="det-bar-fill" style={{ width: `${pct.toFixed(1)}%`, background: "linear-gradient(90deg,var(--accent),var(--accent-2))" }} />
                      </div>
                      <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--muted)", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                        {p.count.toLocaleString()} · {pct.toFixed(1)}%
                      </span>
                    </div>
                  );
                })}
              </div>
            )}

            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", fontSize: 12, color: "var(--muted)" }}>
              <span>
                <Link to="/playlists" style={{ color: "var(--accent-2)" }}>{g.playlists.watched} watched playlist{g.playlists.watched === 1 ? "" : "s"}</Link>
                {g.playlists.total > g.playlists.watched ? ` (${g.playlists.total} total)` : ""}:
              </span>
              <span className="chip chip--have">✓ {g.playlists.have}</span>
              <span className="chip chip--queued">↓ {g.playlists.queued}</span>
              <span className="chip chip--missing">✗ {g.playlists.missing}</span>
              {failed > 0 && (
                <Link to="/queue" className="chip chip--failed" style={{ textDecoration: "none" }}>{failed} failed grab{failed === 1 ? "" : "s"} →</Link>
              )}
              {inbox > 0 && (
                <Link to="/inbox" className="chip chip--warn" style={{ textDecoration: "none" }}>{inbox} in inbox →</Link>
              )}
            </div>
          </div>
        );
      })()}
    </>
  );
}
