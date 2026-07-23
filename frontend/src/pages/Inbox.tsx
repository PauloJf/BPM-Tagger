import { Fragment, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { GrabCandidate, InboxItem } from "../lib/types";
import { useTitle } from "../hooks/useTitle";
import { useGrabberStatus } from "../hooks/useGrabberStatus";
import PageHeader from "../components/PageHeader";
import GrabberGate from "../components/GrabberGate";
import EmptyState from "../components/EmptyState";
import { PreviewButton } from "../components/trackBits";

function durationDelta(itemMs: number | null, candMs: number | null): string {
  if (!itemMs || !candMs) return "—";
  const d = Math.round((candMs - itemMs) / 1000);
  return `${d > 0 ? "+" : ""}${d}s`;
}

function fmtDur(ms: number | null): string {
  if (!ms) return "—";
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function CandidateCard({ cand, item, onChoose, busy }: {
  cand: GrabCandidate; item: InboxItem; onChoose: () => void; busy: boolean;
}) {
  const [open, setOpen] = useState(false);
  let breakdown: Record<string, unknown> = {};
  try { breakdown = cand.score_breakdown ? JSON.parse(cand.score_breakdown) : {}; } catch { /* ignore */ }
  const pct = cand.score != null ? Math.round(cand.score * 100) : 0;
  const itemMs = item.duration_ms;
  const delta = durationDelta(itemMs, cand.duration_ms);
  const deltaBad = delta !== "—" && Math.abs(parseInt(delta)) > 10;

  // Field-by-field comparison of the Spotify source vs this candidate.
  const isrcMatch = !!item.isrc && !!cand.isrc && item.isrc.toUpperCase() === cand.isrc.toUpperCase();
  const compareRows: { label: string; source: string; cand: string; match: boolean }[] = [
    { label: "Title", source: item.title || "—", cand: cand.title || "—", match: (item.title || "").toLowerCase() === (cand.title || "").toLowerCase() },
    { label: "Artist", source: item.artist || "—", cand: cand.artist || "—", match: (item.artist || "").toLowerCase() === (cand.artist || "").toLowerCase() },
    { label: "Album", source: item.album || "—", cand: cand.album || "—", match: (item.album || "").toLowerCase() === (cand.album || "").toLowerCase() },
    { label: "Duration", source: fmtDur(itemMs), cand: fmtDur(cand.duration_ms), match: !deltaBad && delta !== "—" },
    { label: "ISRC", source: item.isrc || "—", cand: cand.isrc || "—", match: isrcMatch },
  ];
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
        {cand.provider === "deezer" && cand.provider_track_id ? (
          <PreviewButton
            track={{ dz_track_id: cand.provider_track_id, title: cand.title || "", artist: cand.artist || "" }}
            resolveUrl={() =>
              api.get<{ preview_url: string }>(`/api/inbox/candidates/${cand.id}/preview`).then((r) => r.preview_url)}
          />
        ) : cand.url ? (
          <a
            className="btn btn-bare btn-sm"
            href={cand.url}
            target="_blank"
            rel="noreferrer"
            title="Open source page"
            style={{ padding: "2px 6px", color: "var(--muted)" }}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
              <path d="M14 5h5v5M19 5l-8 8M11 5H6a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-5" />
            </svg>
          </a>
        ) : null}
        <button className="btn btn-bare btn-sm" onClick={() => setOpen((o) => !o)}>{open ? "Hide" : "Details"}</button>
        <button className="btn btn-primary btn-sm" disabled={busy} onClick={onChoose}>Choose</button>
      </div>
      <div style={{ marginTop: 8, fontSize: 13 }}>
        <span style={{ fontWeight: 500 }}>{cand.title || "—"}</span>
        <span style={{ color: "var(--muted)" }}> · {cand.artist}{cand.album ? ` · ${cand.album}` : ""}</span>
      </div>
      {open && (
        <div style={{ marginTop: 10 }}>
          {/* Source (Spotify) vs candidate, field by field. */}
          <div style={{ display: "grid", gridTemplateColumns: "80px 1fr 1fr", gap: "4px 12px", fontSize: 12, alignItems: "baseline" }}>
            <div />
            <div style={{ fontSize: 11, color: "var(--muted)", fontWeight: 500 }}>Spotify</div>
            <div style={{ fontSize: 11, color: "var(--muted)", fontWeight: 500 }}>Candidate</div>
            {compareRows.map((r) => (
              <Fragment key={r.label}>
                <div style={{ fontSize: 11, color: "var(--muted)" }}>{r.label}</div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)", wordBreak: "break-word" }}>{r.source}</div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: r.match ? "var(--ok-fg)" : "var(--warn-fg)", wordBreak: "break-word" }}>
                  {r.cand}{r.match ? " ✓" : ""}
                </div>
              </Fragment>
            ))}
          </div>
          {Object.keys(breakdown).length > 0 && (
            <div style={{ marginTop: 10, paddingTop: 8, borderTop: "1px solid var(--border)", fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)", display: "flex", gap: 12, flexWrap: "wrap" }}>
              {Object.entries(breakdown).map(([k, v]) => (
                <span key={k}>{k}: <span style={{ color: "var(--text)" }}>{String(v)}</span></span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function InboxCard({ item, expanded, onToggle, onChoose, onSearch, onResearch, onSkip, busy }: {
  item: InboxItem;
  expanded: boolean;
  onToggle: () => void;
  onChoose: (candId: number) => void;
  onSearch: (query: string) => void;
  onResearch: () => void;
  onSkip: () => void;
  busy: boolean;
}) {
  // Pre-fill the edit box with the original query so a tweak-and-retry is easy.
  const [query, setQuery] = useState(`${item.artist || ""} ${item.title || ""}`.trim());
  const [editing, setEditing] = useState(false);
  const bestScore = item.candidates.reduce<number | null>(
    (best, c) => (c.score != null && (best == null || c.score > best) ? c.score : best), null);
  return (
    <div className="card">
      {/* Whole header toggles the card; inner buttons stop propagation. */}
      <div
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onClick={onToggle}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onToggle(); } }}
        style={{ cursor: "pointer" }}
      >
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 4, flexWrap: "wrap" }}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--muted)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
            style={{ alignSelf: "center", flexShrink: 0, transform: expanded ? "rotate(90deg)" : "none", transition: "transform 0.15s" }} aria-hidden>
            <path d="M9 6l6 6-6 6" />
          </svg>
          <span className="badge badge--review"><span className="badge-dot" />needs review</span>
          <div style={{ fontSize: 16, fontWeight: 600 }}>{item.title}</div>
          {item.isrc ? (
            <span onClick={(e) => e.stopPropagation()}>
              <PreviewButton
                track={{ dz_track_id: `src:${item.id}`, title: item.title || "", artist: item.artist || "" }}
                resolveUrl={() =>
                  api.get<{ preview_url: string }>(`/api/inbox/${item.id}/source-preview`).then((r) => r.preview_url)}
              />
            </span>
          ) : null}
          {!expanded && (
            <>
              <div style={{ flex: 1 }} />
              <span className="chip chip--neutral" style={{ alignSelf: "center" }}>
                {item.candidates.length} candidate{item.candidates.length === 1 ? "" : "s"}
              </span>
              {bestScore != null && (
                <span className="chip chip--active" style={{ alignSelf: "center" }}>best {Math.round(bestScore * 100)}%</span>
              )}
            </>
          )}
        </div>
        <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: expanded ? 14 : 0 }}>{item.artist}{item.album ? ` · ${item.album}` : ""}</div>
      </div>

      {expanded && (<>
      <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 12 }}>
        {item.candidates.length === 0 ? (
          <div style={{ color: "var(--muted)", fontSize: 13 }}>No candidates found — try a different search.</div>
        ) : (
          item.candidates.map((c) => (
            <CandidateCard key={c.id} cand={c} item={item} busy={busy} onChoose={() => onChoose(c.id)} />
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
            <button className="btn btn-primary btn-sm" disabled={busy} onClick={onResearch}>Search again</button>
            <button className="btn btn-ghost btn-sm" onClick={() => setEditing(true)}>Edit search</button>
            <button className="btn btn-danger btn-sm" disabled={busy} onClick={() => { if (window.confirm(`Skip "${item.title}"? It will be discarded from the queue.`)) onSkip(); }}>Skip</button>
          </>
        )}
      </div>
      </>)}
    </div>
  );
}

export default function Inbox() {
  useTitle("Inbox");
  const qc = useQueryClient();
  const status = useGrabberStatus();
  // Cards start collapsed so a full inbox scans as a compact list; ids absent
  // from the map are collapsed, so items resolved elsewhere just drop out.
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});

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
  const research = useMutation({ mutationFn: (id: number) => api.post(`/api/inbox/${id}/research`), onSuccess: invalidate });
  const researchAll = useMutation({ mutationFn: () => api.post("/api/inbox/research-all"), onSuccess: invalidate });
  const skip = useMutation({ mutationFn: (id: number) => api.post(`/api/inbox/${id}/skip`), onSuccess: invalidate });
  const busy = choose.isPending || search.isPending || research.isPending || researchAll.isPending || skip.isPending;

  const items = inboxQ.data?.items ?? [];
  const anyExpanded = items.some((it) => expanded[it.id]);
  const toggleAll = () =>
    setExpanded(anyExpanded ? {} : Object.fromEntries(items.map((it) => [it.id, true])));

  return (
    <GrabberGate title="Inbox" subtitle="Ambiguous matches waiting for a decision — choose a candidate, refine the search, or skip.">
      <PageHeader
        title="Inbox"
        subtitle={
          items.length > 0
            ? <><span style={{ fontFamily: "var(--mono)", color: "var(--text)" }}>{items.length}</span> waiting — choose a candidate, refine the search, or skip</>
            : "Ambiguous matches waiting for a decision — choose a candidate, refine the search, or skip."
        }
        actions={items.length > 0 ? (
          <>
            <button className="btn btn-ghost btn-sm" onClick={toggleAll}>
              {anyExpanded ? "Collapse all" : "Expand all"}
            </button>
            <button
              className="btn btn-ghost btn-sm"
              disabled={busy}
              onClick={() => researchAll.mutate()}
              title="Re-run the default search for every item in the inbox"
            >
              {researchAll.isPending ? "Searching…" : `Search all again (${items.length})`}
            </button>
          </>
        ) : undefined}
      />

      {items.length === 0 ? (
        <EmptyState message={inboxQ.isLoading ? "Loading…" : "Nothing needs review."} />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {items.map((it) => (
            <InboxCard
              key={it.id}
              item={it}
              busy={busy}
              expanded={!!expanded[it.id]}
              onToggle={() => setExpanded((m) => ({ ...m, [it.id]: !m[it.id] }))}
              onChoose={(candId) => choose.mutate({ id: it.id, candId })}
              onSearch={(query) => search.mutate({ id: it.id, query })}
              onResearch={() => research.mutate(it.id)}
              onSkip={() => skip.mutate(it.id)}
            />
          ))}
        </div>
      )}
    </GrabberGate>
  );
}
