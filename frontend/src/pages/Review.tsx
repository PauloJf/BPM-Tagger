import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { ReviewPage, Track } from "../lib/types";
import { DetectorBar } from "../components/DetectorBar";
import { useTitle } from "../hooks/useTitle";

const SEP = /[/\\]/;
function pathParts(p: string) {
  const parts = p.split(SEP);
  return {
    fname: parts[parts.length - 1] || p,
    album: parts.length >= 2 ? parts[parts.length - 2] : "",
    artist: parts.length >= 3 ? parts[parts.length - 3] : "",
  };
}

function ReviewCard({ track, idx, total, confThreshold, onApprove, approving }: {
  track: Track;
  idx: number;
  total: number;
  confThreshold: number;
  onApprove: (fp: string) => void;
  approving: boolean;
}) {
  const t = track;
  const { fname, album, artist } = pathParts(t.file_path);
  return (
    <div className="review-card">
      <div className="review-card-grid">
        <div className="review-left">
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 6, flexWrap: "wrap" }}>
            <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)" }}>
              {String(idx).padStart(2, "0")} / {String(total).padStart(2, "0")}
            </span>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {t.status === "error" ? (
                <span className="badge badge--error">
                  <span className="badge-dot" />
                  error
                </span>
              ) : (
                <>
                  {t.needs_review ? (
                    <span className="badge badge--review">
                      <span className="badge-dot" />
                      disagreement
                    </span>
                  ) : null}
                  {t.bpm_confidence != null && t.bpm_confidence < confThreshold ? (
                    <span className="badge badge--review">low confidence</span>
                  ) : null}
                  {t.detector === "librosa" ? <span className="badge badge--neutral">fallback only</span> : null}
                </>
              )}
            </div>
          </div>
          <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 4, color: "var(--text)", wordBreak: "break-word" }}>{fname}</div>
          <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 18, display: "flex", alignItems: "center", gap: 6 }}>
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round">
              <path d="M3 7 a2 2 0 0 1 2 -2 h4 l2 2 h8 a2 2 0 0 1 2 2 v9 a2 2 0 0 1 -2 2 H5 a2 2 0 0 1 -2 -2 Z" />
            </svg>
            <span style={{ fontFamily: "var(--mono)" }}>
              {artist}
              {artist && album ? " / " : ""}
              {album}
            </span>
          </div>
          {t.status === "error" ? (
            <div style={{ padding: "12px 14px", background: "var(--err-bg)", border: "1px solid var(--err-bd)", borderRadius: 10, fontSize: 12, color: "var(--err-fg)", fontFamily: "var(--mono)" }}>
              {t.error_message || "Unknown error"}
            </div>
          ) : (
            <>
              <DetectorBar track={t} />
              <div className="review-legend">
                <span style={{ color: "var(--accent)" }}>
                  ● DR <span style={{ color: "var(--text)" }}>{t.bpm_dr ? t.bpm_dr.toFixed(1) : "—"}</span>
                </span>
                <span style={{ color: "var(--accent-2)" }}>
                  ● ES <span style={{ color: "var(--text)" }}>{t.bpm_es ? t.bpm_es.toFixed(1) : "—"}</span>
                </span>
                <span style={{ color: "var(--info-fg)" }}>
                  ● LB <span style={{ color: "var(--text)" }}>{t.bpm_lb ? t.bpm_lb.toFixed(1) : "—"}</span>
                </span>
                <span style={{ marginLeft: "auto", color: "var(--muted)" }}>
                  conf <span style={{ color: "var(--text)" }}>{t.bpm_confidence != null ? t.bpm_confidence.toFixed(2) : "—"}</span>
                </span>
              </div>
            </>
          )}
        </div>
        <div className="review-right">
          <div>
            <div style={{ fontFamily: "var(--mono)", fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.1em", color: "var(--muted)", marginBottom: 6 }}>Detected BPM</div>
            <span style={{ fontFamily: "var(--mono)", fontSize: 38, fontWeight: 600, letterSpacing: "-0.03em", lineHeight: 1, fontVariantNumeric: "tabular-nums", color: t.status === "error" ? "var(--muted)" : "var(--text)" }}>
              {t.bpm ? t.bpm.toFixed(1) : "—"}
            </span>
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
            <button className="btn btn-ghost btn-sm" style={{ flex: 1, color: "var(--ok-fg)" }} disabled={approving} onClick={() => onApprove(t.file_path)}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M5 12 L10 17 L19 7" />
              </svg>
              Approve
            </button>
            <Link to={`/track?path=${encodeURIComponent(t.file_path)}&back=review`} className="btn btn-primary btn-sm" style={{ flex: 1, textAlign: "center", display: "flex", alignItems: "center", justifyContent: "center", gap: 6 }}>
              Review
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <path d="M5 12 H19 M13 6 L19 12 L13 18" />
              </svg>
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function Review() {
  useTitle("Needs Review");
  const [params, setParams] = useSearchParams();
  const qc = useQueryClient();
  const page = Math.max(1, parseInt(params.get("page") || "1", 10) || 1);

  const reviewQ = useQuery({
    queryKey: ["review", page],
    queryFn: () => api.get<ReviewPage>(`/api/review?page=${page}`),
  });

  const approve = useMutation({
    mutationFn: (fp: string) => api.post<{ ok: boolean }>("/api/approve", { file_path: fp }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["review"] });
      qc.invalidateQueries({ queryKey: ["me"] });
      qc.invalidateQueries({ queryKey: ["tracks"] });
    },
  });

  const data = reviewQ.data;
  const tracks = data?.tracks ?? [];
  const total = data?.total ?? 0;
  const pages = data?.pages ?? 1;

  const pageNums: (number | "…")[] = [];
  for (let p = 1; p <= pages; p++) {
    if (p <= 3 || p > pages - 3 || (p >= page - 2 && p <= page + 2)) pageNums.push(p);
    else if (p === 4 || p === pages - 3) pageNums.push("…");
  }
  function setPage(n: number) {
    setParams({ page: String(n) });
  }

  return (
    <>
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: 28, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 4 }}>Needs review</h1>
        <p style={{ fontSize: 13, color: "var(--muted)" }}>
          <span style={{ fontFamily: "var(--mono)", color: "var(--warn-fg)" }}>{total}</span> tracks flagged · review them one by one or open directly
        </p>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {tracks.length === 0 ? (
          <div style={{ padding: "60px 20px", textAlign: "center", background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 14 }}>
            <div style={{ fontSize: 32, marginBottom: 12, color: "var(--ok-fg)" }}>✓</div>
            <div style={{ fontSize: 14, color: "var(--muted)" }}>{reviewQ.isLoading ? "Loading…" : "No tracks need review."}</div>
          </div>
        ) : (
          tracks.map((t, i) => (
            <ReviewCard
              key={t.file_path}
              track={t}
              idx={(page - 1) * (data?.per_page ?? 50) + i + 1}
              total={total}
              confThreshold={data?.conf_threshold ?? 0.4}
              approving={approve.isPending}
              onApprove={(fp) => approve.mutate(fp)}
            />
          ))
        )}
      </div>

      {pages > 1 && (
        <div className="pagination" style={{ marginTop: 20 }}>
          {page > 1 && <button type="button" onClick={() => setPage(page - 1)}>← Prev</button>}
          {pageNums.map((p, i) =>
            p === "…" ? (
              <span key={`e${i}`}>…</span>
            ) : p === page ? (
              <span key={p} className="current" aria-current="page">
                {p}
              </span>
            ) : (
              <button type="button" key={p} onClick={() => setPage(p)} aria-label={`Page ${p}`}>
                {p}
              </button>
            ),
          )}
          {page < pages && <button type="button" onClick={() => setPage(page + 1)}>Next →</button>}
        </div>
      )}
    </>
  );
}
