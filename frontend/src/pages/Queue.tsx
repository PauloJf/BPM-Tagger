import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { QueueItem } from "../lib/types";
import { useTitle } from "../hooks/useTitle";
import { useGrabberStatus } from "../hooks/useGrabberStatus";

const ACTIVE = new Set(["pending", "searching", "downloading", "transcoding", "tagging", "analyzing_bpm"]);

function statusChip(s: string) {
  if (s === "done") return "chip chip--done";
  if (s === "failed") return "chip chip--failed";
  if (s === "awaiting_user") return "chip chip--warn";
  if (s === "skipped") return "chip chip--neutral";
  return "chip chip--active";
}

function Row({ item, onRetry, onCancel }: { item: QueueItem; onRetry: (id: number) => void; onCancel: (id: number) => void }) {
  const active = ACTIVE.has(item.status);
  return (
    <div className="q-row">
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {item.title || "—"}
        </div>
        <div style={{ fontSize: 11, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {item.artist}{item.provider ? ` · ${item.provider}` : ""}{item.error ? ` · ${item.error}` : ""}
        </div>
        {item.status === "downloading" && (
          <div className="q-prog-track"><div className="q-prog-fill" style={{ width: `${Math.round((item.progress || 0) * 100)}%` }} /></div>
        )}
      </div>
      <div><span className={statusChip(item.status)}>{item.status.replace("_", " ")}</span></div>
      <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)" }}>
        {item.attempts ? `${item.attempts} attempt${item.attempts > 1 ? "s" : ""}` : ""}
      </div>
      <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
        {(item.status === "failed" || item.status === "skipped") && (
          <button className="btn btn-ghost btn-sm" onClick={() => onRetry(item.id)}>Retry</button>
        )}
        {active && (
          <button className="btn btn-danger btn-sm" onClick={() => onCancel(item.id)}>Cancel</button>
        )}
      </div>
    </div>
  );
}

export default function Queue() {
  useTitle("Queue");
  const qc = useQueryClient();
  const status = useGrabberStatus();

  const queueQ = useQuery({
    queryKey: ["queue"],
    queryFn: () => api.get<{ items: QueueItem[]; counts: Record<string, number> }>("/api/queue"),
    enabled: status.data?.enabled === true,
    refetchInterval: 2000,
  });
  const historyQ = useQuery({
    queryKey: ["queue-history"],
    queryFn: () => api.get<{ items: QueueItem[] }>("/api/queue/history"),
    enabled: status.data?.enabled === true,
    refetchInterval: 10000,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["queue"] });
    qc.invalidateQueries({ queryKey: ["queue-history"] });
    qc.invalidateQueries({ queryKey: ["grabber-status"] });
  };
  const retry = useMutation({ mutationFn: (id: number) => api.post(`/api/queue/${id}/retry`), onSuccess: invalidate });
  const cancel = useMutation({ mutationFn: (id: number) => api.post(`/api/queue/${id}/cancel`), onSuccess: invalidate });

  if (status.data && !status.data.enabled) {
    return (
      <>
        <h1 style={{ fontSize: 28, fontWeight: 600, marginBottom: 16 }}>Queue</h1>
        <div className="card" style={{ color: "var(--muted)" }}>The grabber is disabled.</div>
      </>
    );
  }

  const items = queueQ.data?.items ?? [];
  const active = items.filter((i) => ACTIVE.has(i.status));
  const history = historyQ.data?.items ?? [];
  const counts = queueQ.data?.counts ?? {};

  return (
    <>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 28, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 4 }}>Queue</h1>
        <p style={{ fontSize: 13, color: "var(--muted)" }}>
          {Object.entries(counts).map(([k, v]) => `${v} ${k}`).join(" · ") || "Nothing queued."}
        </p>
      </div>

      <div className="tracks-table" style={{ marginBottom: 22 }}>
        {active.length === 0 ? (
          <div className="tracks-row-empty">{queueQ.isLoading ? "Loading…" : "Nothing in progress."}</div>
        ) : (
          active.map((i) => <Row key={i.id} item={i} onRetry={retry.mutate} onCancel={cancel.mutate} />)
        )}
      </div>

      <div className="section-label"><span>History</span></div>
      <div className="tracks-table">
        {history.length === 0 ? (
          <div className="tracks-row-empty">No completed items yet.</div>
        ) : (
          history.map((i) => <Row key={i.id} item={i} onRetry={retry.mutate} onCancel={cancel.mutate} />)
        )}
      </div>

      <p style={{ marginTop: 14, fontSize: 12, color: "var(--muted)" }}>
        Ambiguous matches wait in the <Link to="/playlists" style={{ color: "var(--accent-2)" }}>inbox</Link> (coming in M5).
      </p>
    </>
  );
}
