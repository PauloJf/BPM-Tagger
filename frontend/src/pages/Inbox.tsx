import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { GrabCandidate, InboxItem } from "../lib/types";
import { useTitle } from "../hooks/useTitle";
import { useGrabberStatus } from "../hooks/useGrabberStatus";

function durationDelta(itemMs: number | null, candMs: number | null): string {
  if (!itemMs || !candMs) return "—";
  const d = Math.round((candMs - itemMs) / 1000);
  return `${d > 0 ? "+" : ""}${d}s`;
}

function CandidateCard({ cand, itemMs, onChoose, busy }: {
  cand: GrabCandidate; itemMs: number | null; onChoose: () => void; busy: boolean;
}) {
  const [open, setOpen] = useState(false);
  let breakdown: Record<string, unknown> = {};
  try { breakdown = cand.score_breakdown ? JSON.parse(cand.score_breakdown) : {}; } catch { /* ignore */ }
  const pct = cand.score != null ? Math.round(cand.score * 100) : 0;
  const delta = durationDelta(itemMs, cand.duration_ms);
  const deltaBad = delta !== "—" && Math.abs(parseInt(delta)) > 10;
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 10, padding: "12px 14px", background: "var(--surface-2)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span className="chip chip--neutral">{cand.provider}</span>
        {cand.quality && <span className="chip chip--neutral">{cand.quality}</span>}
        <span className="chip chip--active">{pct}%</span>
        <span className="chip" style={{ borderColor: deltaBad ? "var(--err-bd)" : "var(--border)", color: deltaBad ? "var(--err-fg)" : "var(--muted)", background: "var(--chip-bg)" }}>
          Δ {delta}
        </span>
        <div style={{ flex: 1 }} />
        <button className="btn btn-bare btn-sm" onClick={() => setOpen((o) => !o)}>{open ? "Hide" : "Details"}</button>
        <button className="btn btn-primary btn-sm" disabled={busy} onClick={onChoose}>Choose</button>
      </div>
      <div style={{ marginTop: 8, fontSize: 13 }}>
        <span style={{ fontWeight: 500 }}>{cand.title || "—"}</span>
        <span style={{ color: "var(--muted)" }}> · {cand.artist}{cand.album ? ` · ${cand.album}` : ""}</span>
      </div>
      {open && (
        <div style={{ marginTop: 8, fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)", display: "flex", gap: 12, flexWrap: "wrap" }}>
          {Object.entries(breakdown).map(([k, v]) => (
            <span key={k}>{k}: <span style={{ color: "var(--text)" }}>{String(v)}</span></span>
          ))}
        </div>
      )}
    </div>
  );
}

function InboxCard({ item, onChoose, onSearch, onSkip, busy }: {
  item: InboxItem;
  onChoose: (candId: number) => void;
  onSearch: (query: string) => void;
  onSkip: () => void;
  busy: boolean;
}) {
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState(false);
  return (
    <div className="card">
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 4, flexWrap: "wrap" }}>
        <span className="badge badge--review"><span className="badge-dot" />needs review</span>
        <div style={{ fontSize: 16, fontWeight: 600 }}>{item.title}</div>
      </div>
      <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 14 }}>{item.artist}{item.album ? ` · ${item.album}` : ""}</div>

      <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 12 }}>
        {item.candidates.length === 0 ? (
          <div style={{ color: "var(--muted)", fontSize: 13 }}>No candidates found — try a different search.</div>
        ) : (
          item.candidates.map((c) => (
            <CandidateCard key={c.id} cand={c} itemMs={item.duration_ms} busy={busy} onChoose={() => onChoose(c.id)} />
          ))
        )}
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        {editing ? (
          <>
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="new search query"
              autoFocus
              style={{ flex: 1, minWidth: 200, fontFamily: "var(--mono)", fontSize: 12 }}
              onKeyDown={(e) => { if (e.key === "Enter" && query.trim()) onSearch(query.trim()); }}
            />
            <button className="btn btn-primary btn-sm" disabled={!query.trim() || busy} onClick={() => onSearch(query.trim())}>Re-search</button>
            <button className="btn btn-bare btn-sm" onClick={() => setEditing(false)}>Cancel</button>
          </>
        ) : (
          <>
            <button className="btn btn-ghost btn-sm" onClick={() => setEditing(true)}>Edit search</button>
            <button className="btn btn-danger btn-sm" disabled={busy} onClick={onSkip}>Skip</button>
          </>
        )}
      </div>
    </div>
  );
}

export default function Inbox() {
  useTitle("Inbox");
  const qc = useQueryClient();
  const status = useGrabberStatus();

  const inboxQ = useQuery({
    queryKey: ["inbox"],
    queryFn: () => api.get<{ items: InboxItem[] }>("/api/inbox"),
    enabled: status.data?.enabled === true,
    refetchInterval: 5000,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["inbox"] });
    qc.invalidateQueries({ queryKey: ["grabber-status"] });
    qc.invalidateQueries({ queryKey: ["queue"] });
  };
  const choose = useMutation({ mutationFn: (v: { id: number; candId: number }) => api.post(`/api/inbox/${v.id}/choose`, { candidate_id: v.candId }), onSuccess: invalidate });
  const search = useMutation({ mutationFn: (v: { id: number; query: string }) => api.post(`/api/inbox/${v.id}/search`, { query: v.query }), onSuccess: invalidate });
  const skip = useMutation({ mutationFn: (id: number) => api.post(`/api/inbox/${id}/skip`), onSuccess: invalidate });
  const busy = choose.isPending || search.isPending || skip.isPending;

  if (status.data && !status.data.enabled) {
    return (
      <>
        <h1 style={{ fontSize: 28, fontWeight: 600, marginBottom: 16 }}>Inbox</h1>
        <div className="card" style={{ color: "var(--muted)" }}>The grabber is disabled.</div>
      </>
    );
  }

  const items = inboxQ.data?.items ?? [];

  return (
    <>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 28, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 4 }}>Inbox</h1>
        <p style={{ fontSize: 13, color: "var(--muted)" }}>
          Ambiguous matches waiting for a decision — choose a candidate, refine the search, or skip.
        </p>
      </div>

      {items.length === 0 ? (
        <div style={{ padding: "60px 20px", textAlign: "center", background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 14 }}>
          <div style={{ fontSize: 32, marginBottom: 12, color: "var(--ok-fg)" }}>✓</div>
          <div style={{ fontSize: 14, color: "var(--muted)" }}>{inboxQ.isLoading ? "Loading…" : "Nothing needs review."}</div>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {items.map((it) => (
            <InboxCard
              key={it.id}
              item={it}
              busy={busy}
              onChoose={(candId) => choose.mutate({ id: it.id, candId })}
              onSearch={(query) => search.mutate({ id: it.id, query })}
              onSkip={() => skip.mutate(it.id)}
            />
          ))}
        </div>
      )}
    </>
  );
}
