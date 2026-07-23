import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { QueueItem } from "../lib/types";
import { useTitle } from "../hooks/useTitle";
import PageHeader from "../components/PageHeader";
import GrabberGate from "../components/GrabberGate";
import { useGrabberStatus } from "../hooks/useGrabberStatus";

const ACTIVE = new Set(["pending", "searching", "downloading", "transcoding", "tagging", "analyzing_bpm"]);

function statusChip(s: string) {
  if (s === "done") return "chip chip--done";
  if (s === "failed") return "chip chip--failed";
  if (s === "awaiting_user") return "chip chip--warn";
  if (s === "skipped") return "chip chip--neutral";
  return "chip chip--active";
}

const inheritLink: React.CSSProperties = { color: "inherit", textDecoration: "none" };

function Row({ item, onRetry, onCancel, onDelete }: { item: QueueItem; onRetry: (id: number) => void; onCancel: (id: number) => void; onDelete: (id: number) => void }) {
  const active = ACTIVE.has(item.status);
  const removable = item.status === "failed" || item.status === "skipped";
  // A completed grab has been filed into the library — link its title/album
  // straight to the track/album pages (same inherit-color link style as the
  // playlist rows); those pages need the file on disk, so unfiled items stay
  // plain text. The artist page is useful even for artists you don't own yet
  // (Related panel, Browse Deezer), so the artist always links.
  const inLibrary = item.status === "done" && !!item.final_path;
  return (
    <div className="q-row">
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {inLibrary ? (
            <Link to={`/track?path=${encodeURIComponent(item.final_path!)}`} style={inheritLink} title="Open the track page">
              {item.title || "—"}
            </Link>
          ) : (
            item.title || "—"
          )}
        </div>
        <div style={{ fontSize: 11, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {item.artist ? (
            <Link to={`/artist?name=${encodeURIComponent(item.artist)}`} style={inheritLink} title={`View ${item.artist}`}>{item.artist}</Link>
          ) : (
            item.artist
          )}
          {inLibrary && item.album && (
            <> · <Link to={`/album?album=${encodeURIComponent(item.album)}&album_artist=${encodeURIComponent(item.album_artist || item.artist || "")}`} style={inheritLink} title={`View ${item.album}`}>{item.album}</Link></>
          )}
          {item.provider ? ` · ${item.provider}` : ""}{item.error ? ` · ${item.error}` : ""}
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
        {removable && (
          <button className="btn btn-ghost btn-sm" onClick={() => onRetry(item.id)}>Retry</button>
        )}
        {removable && (
          <button
            className="btn btn-ghost btn-sm"
            style={{ color: "var(--err-fg)" }}
            title="Remove this item from the queue (does not delete any downloaded file)"
            onClick={() => onDelete(item.id)}
          >
            Delete
          </button>
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
  const del = useMutation({ mutationFn: (id: number) => api.del(`/api/queue/${id}`), onSuccess: invalidate });
  const retryAll = useMutation({ mutationFn: () => api.post("/api/queue/retry-failed"), onSuccess: invalidate });
  const clearDone = useMutation({ mutationFn: () => api.post("/api/queue/clear-completed"), onSuccess: invalidate });

  const items = queueQ.data?.items ?? [];
  const active = items.filter((i) => ACTIVE.has(i.status));
  const history = historyQ.data?.items ?? [];
  const counts = queueQ.data?.counts ?? {};

  return (
    <GrabberGate title="Queue" subtitle="The grabber is disabled.">
      <PageHeader
        title="Queue"
        subtitle={Object.entries(counts).map(([k, v]) => `${v} ${k}`).join(" · ") || "Nothing queued."}
        actions={((counts.failed ?? 0) > 0 || (counts.done ?? 0) > 0) ? (
          <>
            {(counts.failed ?? 0) > 0 && (
              <button
                className="btn btn-ghost btn-sm"
                disabled={retryAll.isPending}
                onClick={() => retryAll.mutate()}
                title="Re-queue every failed item and search again"
              >
                {retryAll.isPending ? "Retrying…" : `Retry all failed (${counts.failed})`}
              </button>
            )}
            {(counts.done ?? 0) > 0 && (
              <button
                className="btn btn-ghost btn-sm"
                style={{ color: "var(--muted)" }}
                disabled={clearDone.isPending}
                onClick={() => clearDone.mutate()}
                title="Remove every completed item from the history (downloaded files are kept)"
              >
                {clearDone.isPending ? "Clearing…" : `Clear completed (${counts.done})`}
              </button>
            )}
          </>
        ) : undefined}
      />

      <div className="tracks-table" style={{ marginBottom: 22 }}>
        {active.length === 0 ? (
          <div className="tracks-row-empty">{queueQ.isLoading ? "Loading…" : "Nothing in progress."}</div>
        ) : (
          active.map((i) => <Row key={i.id} item={i} onRetry={retry.mutate} onCancel={cancel.mutate} onDelete={del.mutate} />)
        )}
      </div>

      <div className="section-label"><span>History</span></div>
      <div className="tracks-table">
        {history.length === 0 ? (
          <div className="tracks-row-empty">No completed items yet.</div>
        ) : (
          history.map((i) => <Row key={i.id} item={i} onRetry={retry.mutate} onCancel={cancel.mutate} onDelete={del.mutate} />)
        )}
      </div>

      <p style={{ marginTop: 14, fontSize: 12, color: "var(--muted)" }}>
        Ambiguous matches wait in the <Link to="/inbox" style={{ color: "var(--accent-2)" }}>inbox</Link>.
      </p>
    </GrabberGate>
  );
}
