import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useTitle } from "../hooks/useTitle";

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
  };
  bpm_descriptive: { avg: number | null; median: number | null; min: number | null; max: number | null };
  bpm_distribution: { bpm: number; count: number }[];
  detector_distribution: { detector: string; count: number }[];
}

const fmt = (v: number | null, dec = 0) => (v == null ? "—" : Number(v).toFixed(dec));
const num = (v: number | undefined) => (v || 0).toLocaleString();

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
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: 28, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 4 }}>Statistics</h1>
        <p style={{ fontSize: 13, color: "var(--muted)" }}>Library health, BPM distribution, and detector performance.</p>
      </div>

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

    </>
  );
}
