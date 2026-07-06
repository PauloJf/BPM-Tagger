import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { basename, parentName } from "../lib/paths";
import type { Progress, TracksPage } from "../lib/types";
import { ArrowIcon, ConfBar, FolderIcon, StatusBadge } from "../components/trackBits";
import { useTitle } from "../hooks/useTitle";
import { usePlayer } from "../lib/player";

const STEPS = ["deeprhythm", "essentia", "librosa"];

function ScanBanner({ p }: { p: Progress }) {
  const pct = p.total > 0 ? Math.round((p.completed / p.total) * 100) : 0;
  const activeIdx = STEPS.indexOf(p.current_step);
  const cumText = p.cumulative_completed > p.completed ? ` · ${p.cumulative_completed} total` : "";
  return (
    <div style={{ background: "var(--surface-2)", border: "1px solid var(--accent-border)", borderRadius: 14, padding: "14px 18px", marginBottom: 22, position: "relative", overflow: "hidden" }}>
      <div style={{ position: "absolute", inset: 0, pointerEvents: "none", background: "radial-gradient(600px circle at 20% 0%, var(--accent-glow), transparent 60%)" }} />
      <div style={{ position: "relative", display: "flex", alignItems: "center", gap: 14, marginBottom: 10, flexWrap: "wrap" }}>
        <span className="scan-dot pulsing" style={{ background: "var(--accent)", boxShadow: "0 0 0 4px var(--accent-glow)" }} />
        <span style={{ fontSize: 12, fontWeight: 600, color: "var(--accent-2)", letterSpacing: "0.02em" }}>ANALYZING</span>
        <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)" }}>
          batch · {p.completed} / {p.total} · {pct}%{cumText}
        </span>
        <div style={{ flex: 1 }} />
        <div style={{ display: "flex", gap: 6 }}>
          {STEPS.map((s, i) => (
            <span key={s} className={"scan-step" + (i < activeIdx ? " done" : i === activeIdx ? " active" : "")}>
              {s}
            </span>
          ))}
        </div>
      </div>
      <div style={{ position: "relative", display: "flex", alignItems: "baseline", gap: 6, marginBottom: 12 }}>
        <span style={{ fontFamily: "var(--mono)", fontSize: 13, color: "var(--text)" }}>
          {p.current_file ? basename(p.current_file) : "…"}
        </span>
      </div>
      <div style={{ position: "relative", height: 3, background: "var(--border)", borderRadius: 2, overflow: "hidden", marginBottom: 10 }}>
        <div style={{ height: "100%", width: `${pct}%`, background: "linear-gradient(90deg, var(--accent), var(--accent-2))", transition: "width 0.6s ease", boxShadow: "0 0 8px var(--accent-glow)" }} />
      </div>
      <div style={{ fontSize: 11, color: "var(--muted)", display: "flex", justifyContent: "space-between" }}>
        <span>
          Last · <span style={{ color: "var(--text)", fontFamily: "var(--mono)" }}>{p.last_file ? basename(p.last_file) : ""}</span>
        </span>
        <span style={{ fontFamily: "var(--mono)", color: "var(--accent-2)" }}>{p.last_bpm != null ? `${p.last_bpm.toFixed(1)} BPM` : ""}</span>
      </div>
    </div>
  );
}

const SearchIcon = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
    <circle cx="11" cy="11" r="6" />
    <path d="M16 16 L21 21" />
  </svg>
);

export default function Tracks() {
  useTitle("Library");
  const [params, setParams] = useSearchParams();
  const qc = useQueryClient();
  const player = usePlayer();

  const q = params.get("q") || "";
  const filter = params.get("filter") || "";
  const bpm = params.get("bpm") || "";
  const bpmTol = params.get("bpm_tol") || "5";
  const page = Math.max(1, parseInt(params.get("page") || "1", 10) || 1);
  const perPage = params.get("per_page") || "50";

  // Local input state (debounced into the URL).
  const [searchText, setSearchText] = useState(q);
  const [bpmText, setBpmText] = useState(bpm);
  const [tolText, setTolText] = useState(bpmTol);

  // Keep local inputs in sync when the URL changes externally (e.g. Clear).
  useEffect(() => setSearchText(q), [q]);
  useEffect(() => setBpmText(bpm), [bpm]);

  const debounce = useRef<number | undefined>(undefined);
  function scheduleApply(next: { q?: string; bpm?: string; tol?: string }) {
    window.clearTimeout(debounce.current);
    debounce.current = window.setTimeout(() => {
      setParams((prev) => {
        const p = new URLSearchParams(prev);
        const qv = (next.q ?? searchText).trim();
        const bv = (next.bpm ?? bpmText).trim();
        const tv = (next.tol ?? tolText).trim() || "5";
        if (qv) p.set("q", qv);
        else p.delete("q");
        if (bv) {
          p.set("bpm", bv);
          p.set("bpm_tol", tv);
        } else {
          p.delete("bpm");
          p.delete("bpm_tol");
        }
        p.set("page", "1");
        return p;
      }, { replace: true });
    }, 300);
  }

  function setPage(next: number) {
    setParams((prev) => {
      const p = new URLSearchParams(prev);
      p.set("page", String(next));
      return p;
    });
  }

  function setPerPage(val: string) {
    setParams((prev) => {
      const p = new URLSearchParams(prev);
      p.set("per_page", val);
      p.set("page", "1");
      return p;
    });
  }

  function setFilter(f: string) {
    setParams((prev) => {
      const p = new URLSearchParams(prev);
      if (f) p.set("filter", f);
      else p.delete("filter");
      p.set("page", "1");
      return p;
    });
  }

  const search = params.toString();
  const tracksQ = useQuery({
    queryKey: ["tracks", search],
    queryFn: () => api.get<TracksPage>(`/api/tracks?${search}`),
    placeholderData: (prev) => prev,
    // Mirror the Jinja page's freshness: refresh on tab focus and every 30s
    // (so edits/scan results made elsewhere show up). Paused while hidden.
    refetchOnWindowFocus: true,
    refetchInterval: 30_000,
  });

  // Scan progress banner + live table refresh while scanning.
  const progressQ = useQuery({
    queryKey: ["progress"],
    queryFn: () => api.get<Progress>("/api/progress"),
    refetchInterval: 1500,
  });
  const prog = progressQ.data;
  const lastFileRef = useRef("");
  const wasScanningRef = useRef(false);
  useEffect(() => {
    if (!prog) return;
    if (prog.is_scanning) {
      wasScanningRef.current = true;
      if (prog.last_file && prog.last_file !== lastFileRef.current) {
        lastFileRef.current = prog.last_file;
        qc.invalidateQueries({ queryKey: ["tracks"] });
      }
    } else if (wasScanningRef.current) {
      wasScanningRef.current = false;
      qc.invalidateQueries({ queryKey: ["tracks"] });
    }
  }, [prog, qc]);

  const data = tracksQ.data;
  const tracks = data?.tracks ?? [];

  function trackHref(filePath: string) {
    const sp = new URLSearchParams();
    sp.set("path", filePath);
    sp.set("back", "tracks");
    if (filter) sp.set("back_filter", filter);
    if (page > 1) sp.set("back_page", String(page));
    if (q) sp.set("back_q", q);
    if (perPage) sp.set("back_per_page", perPage);
    return `/track?${sp.toString()}`;
  }

  const pills = [
    { key: "", label: "All", count: data?.all_count },
    { key: "review", label: "Review", count: data?.review_count },
    { key: "locked", label: "Locked", count: data?.locked_count },
    { key: "deleted", label: "Deleted", count: data?.deleted_count },
  ];

  const pages = data?.pages ?? 1;
  const pageNums: (number | "…")[] = [];
  for (let p = 1; p <= pages; p++) {
    if (p <= 3 || p > pages - 3 || (p >= page - 2 && p <= page + 2)) pageNums.push(p);
    else if (p === 4 || p === pages - 3) pageNums.push("…");
  }

  return (
    <>
      {prog?.is_scanning && <ScanBanner p={prog} />}

      <div style={{ display: "flex", alignItems: "flex-end", gap: 18, marginBottom: 20, flexWrap: "wrap" }}>
        <div>
          <h1 style={{ fontSize: 28, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 4 }}>Library</h1>
          <p style={{ fontSize: 13, color: "var(--muted)" }}>
            <span style={{ fontFamily: "var(--mono)", color: "var(--text)" }}>{data?.total ?? 0}</span> tracks
          </p>
        </div>

        <div style={{ flex: 1 }} />

        <div className="filter-pills">
          {pills.map((pl) => (
            <button
              key={pl.key}
              className={"filter-pill" + (filter === pl.key ? " active" : "")}
              onClick={() => setFilter(pl.key)}
            >
              {pl.label}
              <span className="pill-count">{pl.count ?? 0}</span>
            </button>
          ))}
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <div className="search-wrap">
            <span className="search-icon">
              <SearchIcon />
            </span>
            <input
              type="text"
              value={searchText}
              placeholder="Search filename or artist…"
              className="search-input"
              autoComplete="off"
              onChange={(e) => {
                setSearchText(e.target.value);
                scheduleApply({ q: e.target.value });
              }}
            />
          </div>
          {(q || bpm) && (
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => {
                setSearchText("");
                setBpmText("");
                setParams(new URLSearchParams(), { replace: true });
              }}
            >
              Clear
            </button>
          )}
          <div style={{ display: "flex", alignItems: "center", gap: 4, flexShrink: 0 }}>
            <input
              type="number"
              placeholder="BPM"
              min={1}
              max={300}
              step={1}
              value={bpmText}
              autoComplete="off"
              style={{ width: 72, padding: "7px 10px", fontFamily: "var(--mono)", fontSize: 13, fontWeight: 600, textAlign: "center", fontVariantNumeric: "tabular-nums" }}
              onChange={(e) => {
                setBpmText(e.target.value);
                scheduleApply({ bpm: e.target.value });
              }}
            />
            <span style={{ fontSize: 11, color: "var(--muted)", fontFamily: "var(--mono)" }}>±</span>
            <input
              type="number"
              min={0}
              max={50}
              step={1}
              value={tolText}
              autoComplete="off"
              style={{ width: 48, padding: "7px 8px", fontFamily: "var(--mono)", fontSize: 13, textAlign: "center", fontVariantNumeric: "tabular-nums" }}
              onChange={(e) => {
                setTolText(e.target.value);
                scheduleApply({ tol: e.target.value });
              }}
            />
          </div>
          <select value={perPage} onChange={(e) => setPerPage(e.target.value)} style={{ fontSize: 12 }}>
            <option value="10">10/page</option>
            <option value="50">50/page</option>
            <option value="100">100/page</option>
          </select>
        </div>
      </div>

      <div className="tracks-table">
        <div className="tracks-header">
          <span>Track</span>
          <span style={{ textAlign: "right" }}>BPM</span>
          <span>Conf.</span>
          <span>Detector</span>
          <span>Status</span>
          <span />
        </div>
        <div>
          {tracks.length === 0 ? (
            <div className="tracks-row-empty">{tracksQ.isLoading ? "Loading…" : "No tracks found."}</div>
          ) : (
            tracks.map((t) => {
              const flagged = (t.needs_review && !t.reviewed) || t.status === "error";
              return (
                <Link key={t.file_path} to={trackHref(t.file_path)} className={"tracks-row" + (flagged ? " flagged" : "")}>
                  <div style={{ minWidth: 0, display: "flex", alignItems: "center", gap: 10 }}>
                    <button
                      className="row-play"
                      aria-label="Play"
                      onClick={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        player.play({ path: t.file_path, title: basename(t.file_path), artist: t.artist || "" });
                      }}
                    >
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="6,4 20,12 6,20" /></svg>
                    </button>
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontSize: 13, color: "var(--text)", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", marginBottom: 3 }}>
                        {basename(t.file_path)}
                      </div>
                      <div style={{ fontSize: 11, color: "var(--muted)", display: "flex", alignItems: "center", gap: 6 }}>
                        <FolderIcon />
                        <span style={{ fontFamily: "var(--mono)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {parentName(t.file_path)}
                        </span>
                      </div>
                    </div>
                  </div>
                  <div style={{ textAlign: "right" }}>
                    <span style={{ fontFamily: "var(--mono)", fontSize: 16, fontWeight: 600, color: t.bpm ? "var(--text)" : "var(--muted)", fontVariantNumeric: "tabular-nums" }}>
                      {t.bpm ? t.bpm.toFixed(1) : "—"}
                    </span>
                  </div>
                  <div>
                    <ConfBar value={t.bpm_confidence} />
                  </div>
                  <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {t.detector || "—"}
                  </div>
                  <div>
                    <StatusBadge track={t} />
                  </div>
                  <div style={{ textAlign: "right", color: "var(--muted)" }}>
                    <ArrowIcon />
                  </div>
                </Link>
              );
            })
          )}
        </div>
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 14, fontSize: 12, color: "var(--muted)" }}>
        <span>
          Showing <span style={{ color: "var(--text)", fontFamily: "var(--mono)" }}>{tracks.length}</span> of{" "}
          <span style={{ fontFamily: "var(--mono)" }}>{data?.total ?? 0}</span> total
        </span>
        {pages > 1 && (
          <div className="pagination" style={{ marginTop: 0 }}>
            {page > 1 && <a onClick={() => setPage(page - 1)}>← Prev</a>}
            {pageNums.map((p, i) =>
              p === "…" ? (
                <span key={`e${i}`}>…</span>
              ) : p === page ? (
                <span key={p} className="current">
                  {p}
                </span>
              ) : (
                <a key={p} onClick={() => setPage(p)}>
                  {p}
                </a>
              ),
            )}
            {page < pages && <a onClick={() => setPage(page + 1)}>Next →</a>}
          </div>
        )}
      </div>
    </>
  );
}
