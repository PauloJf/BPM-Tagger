import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { basename, parentName } from "../lib/paths";
import type { Progress, TracksPage } from "../lib/types";
import { ArrowIcon, ConfBar, FolderIcon, StatusBadge, trackSubtitle, trackTitle } from "../components/trackBits";
import AddToPlaylistMenu from "../components/AddToPlaylistMenu";
import LibraryTabs from "../components/LibraryTabs";
import { ArtToggle, Cover, useArtwork } from "../components/Artwork";
import { useTitle } from "../hooks/useTitle";
import PageHeader from "../components/PageHeader";
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
  const [showArt, toggleArt] = useArtwork();

  const q = params.get("q") || "";
  const filter = params.get("filter") || "";
  const bpm = params.get("bpm") || "";
  const bpmTol = params.get("bpm_tol") || "5";
  const cadence = params.get("bpm_cadence") === "1";
  const page = Math.max(1, parseInt(params.get("page") || "1", 10) || 1);
  const perPage = params.get("per_page") || "50";

  // Local input state (debounced into the URL).
  const [searchText, setSearchText] = useState(q);
  const [bpmText, setBpmText] = useState(bpm);
  const [tolText, setTolText] = useState(bpmTol);
  // Mobile only: the advanced filters (BPM/cadence, per-page, artwork toggle)
  // collapse behind a "Filters" button; on desktop they're always inline.
  const [filtersOpen, setFiltersOpen] = useState(false);

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

  function setCadence(on: boolean) {
    setParams((prev) => {
      const p = new URLSearchParams(prev);
      if (on) p.set("bpm_cadence", "1");
      else p.delete("bpm_cadence");
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

  const [queuing, setQueuing] = useState(false);
  async function playAll(shuffleMode: boolean) {
    setQueuing(true);
    try {
      const sp = new URLSearchParams();
      if (q) sp.set("q", q);
      if (filter) sp.set("filter", filter);
      if (bpm) { sp.set("bpm", bpm); sp.set("bpm_tol", bpmTol); if (cadence) sp.set("bpm_cadence", "1"); }
      const res = await api.get<{ tracks: { file_path: string; artist: string | null }[] }>(
        `/api/tracks/paths?${sp.toString()}`);
      const tracks = res.tracks.map((t) => ({
        path: t.file_path, title: basename(t.file_path), artist: t.artist || "",
      }));
      if (tracks.length) player.playQueue(tracks, 0, { shuffle: shuffleMode });
    } finally {
      setQueuing(false);
    }
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

  async function toggleStar(path: string, starred: boolean) {
    // Optimistic flip in the cache; the invalidate reconciles with the server.
    qc.setQueryData<TracksPage>(["tracks", search], (d) => d && {
      ...d,
      starred_count: Math.max(0, (d.starred_count ?? 0) + (starred ? 1 : -1)),
      tracks: d.tracks.map((t) => (t.file_path === path ? { ...t, starred: starred ? 1 : 0 } : t)),
    });
    try {
      await api.post("/api/track/star", { path, starred });
    } finally {
      qc.invalidateQueries({ queryKey: ["tracks"] });
    }
  }

  async function toggleDislike(path: string, disliked: boolean) {
    qc.setQueryData<TracksPage>(["tracks", search], (d) => d && {
      ...d,
      disliked_count: Math.max(0, (d.disliked_count ?? 0) + (disliked ? 1 : -1)),
      tracks: d.tracks.map((t) => (t.file_path === path ? { ...t, disliked: disliked ? 1 : 0 } : t)),
    });
    try {
      await api.post("/api/track/dislike", { path, disliked });
    } finally {
      qc.invalidateQueries({ queryKey: ["tracks"] });
    }
  }

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
    { key: "starred", label: "Starred", count: data?.starred_count },
    { key: "disliked", label: "Disliked", count: data?.disliked_count },
    { key: "review", label: "Review", count: data?.review_count },
    { key: "locked", label: "Locked", count: data?.locked_count },
    { key: "no_isrc", label: "No ISRC", count: data?.no_isrc_count },
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

      <PageHeader
        title="Library"
        subtitle={<><span style={{ fontFamily: "var(--mono)", color: "var(--text)" }}>{data?.total ?? 0}</span> tracks</>}
        tabs={<LibraryTabs />}
        actions={<>
          <button
            className="btn btn-primary btn-sm"
            disabled={queuing || !data?.total}
            onClick={() => playAll(false)}
            title="Play every track in this view"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" style={{ marginRight: 4 }}><polygon points="6,4 20,12 6,20" /></svg>
            Play all
          </button>
          <button
            className="btn btn-ghost btn-sm"
            disabled={queuing || !data?.total}
            onClick={() => playAll(true)}
            title="Shuffle every track in this view"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ marginRight: 4 }}><path d="M16 3h5v5M4 20L21 3M21 16v5h-5M15 15l6 6M4 4l5 5" /></svg>
            Shuffle
          </button>
        </>}
      />

      <div className="lib-toolbar">
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

        <div className="lib-search-row">
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
          <button
            type="button"
            className="btn btn-ghost btn-sm lib-filters-toggle"
            aria-expanded={filtersOpen}
            onClick={() => setFiltersOpen((o) => !o)}
            title="Show BPM, cadence and display filters"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" style={{ marginRight: 5 }}><path d="M4 6h16M7 12h10M10 18h4" /></svg>
            Filters{bpm && <span className="pill-count" style={{ marginLeft: 6 }}>1</span>}
          </button>
        </div>
        <div className={"lib-adv-filters" + (filtersOpen ? " open" : "")}>
          <ArtToggle show={showArt} onToggle={toggleArt} />
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
            <label
              style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, color: cadence ? "var(--accent-2)" : "var(--muted)", cursor: bpm ? "pointer" : "default", opacity: bpm ? 1 : 0.5, whiteSpace: "nowrap" }}
              title="Also match half- and double-time tracks (running cadence)"
            >
              <input type="checkbox" checked={cadence} disabled={!bpm} onChange={(e) => setCadence(e.target.checked)} />
              cadence ½×/2×
            </label>
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
              const title = trackTitle(t);
              const subtitle = trackSubtitle(t);
              const folder = parentName(t.file_path);
              const meta = { path: t.file_path, title, artist: t.artist || "", bpm: t.bpm };
              return (
                <Link key={t.file_path} to={trackHref(t.file_path)} className={"tracks-row" + (flagged ? " flagged" : "")}>
                  <div className="col-track" style={{ minWidth: 0, display: "flex", alignItems: "center", gap: 10 }}>
                    <button
                      className="row-play"
                      aria-label="Play"
                      onClick={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        player.play(meta);
                      }}
                    >
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="6,4 20,12 6,20" /></svg>
                    </button>
                    <button
                      className="row-play row-extra-btn"
                      aria-label="Add to queue"
                      title="Add to queue"
                      onClick={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        player.enqueue(meta);
                      }}
                    >
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"><path d="M12 5v14M5 12h14" /></svg>
                    </button>
                    <button
                      className="row-play row-extra-btn"
                      aria-label="Play next"
                      title="Play next"
                      onClick={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        player.playNext(meta);
                      }}
                    >
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="5,4 13,12 5,20" /><rect x="14" y="4" width="2.5" height="16" rx="1" /></svg>
                    </button>
                    <button
                      /* Stays visible on mobile only when starred (row-extra-btn hides there). */
                      className={"row-play" + (t.starred ? "" : " row-extra-btn")}
                      style={t.starred ? { color: "var(--warn-fg)", borderColor: "var(--warn-fg)" } : undefined}
                      aria-label={t.starred ? "Unstar" : "Star"}
                      aria-pressed={!!t.starred}
                      title={t.starred ? "Unstar" : "Star — preferred when building run queues"}
                      onClick={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        toggleStar(t.file_path, !t.starred);
                      }}
                    >
                      <svg width="12" height="12" viewBox="0 0 24 24" fill={t.starred ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round">
                        <polygon points="12,2.5 15,9 22,9.8 17,14.6 18.2,21.6 12,18.2 5.8,21.6 7,14.6 2,9.8 9,9" />
                      </svg>
                    </button>
                    <button
                      /* Stays visible on mobile only when disliked (row-extra-btn hides there). */
                      className={"row-play" + (t.disliked ? "" : " row-extra-btn")}
                      style={t.disliked ? { color: "var(--err-fg)", borderColor: "var(--err-fg)" } : undefined}
                      aria-label={t.disliked ? "Remove dislike" : "Dislike"}
                      aria-pressed={!!t.disliked}
                      title={t.disliked ? "Remove dislike — eligible for run queues again" : "Dislike — never picked for a run again"}
                      onClick={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        toggleDislike(t.file_path, !t.disliked);
                      }}
                    >
                      <svg width="12" height="12" viewBox="0 0 24 24" fill={t.disliked ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17" />
                      </svg>
                    </button>
                    {/* Add-to-playlist: hidden on mobile (row-extra-btn) like the
                        other secondary row actions, so the tight phone row stays clean. */}
                    <AddToPlaylistMenu path={t.file_path} className="row-play row-extra-btn" title="Add to playlist" />
                    {showArt && (
                      <span className="row-cover">
                        <Cover path={t.file_path} size={38} />
                      </span>
                    )}
                    <div style={{ minWidth: 0 }}>
                      <div className="track-main" style={{ fontSize: 13, color: "var(--text)", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {title}
                      </div>
                      {subtitle && (
                        <div className="track-sub" style={{ fontSize: 11, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", marginTop: 3 }}>
                          {subtitle}
                        </div>
                      )}
                      {folder && (
                        <div className="track-folder" style={{ fontSize: 11, color: "var(--muted)", marginTop: 3 }}>
                          <FolderIcon />
                          <span style={{ fontFamily: "var(--mono)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {folder}
                          </span>
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="col-bpm" style={{ textAlign: "right" }}>
                    <span style={{ fontFamily: "var(--mono)", fontSize: 16, fontWeight: 600, color: t.bpm ? "var(--text)" : "var(--muted)", fontVariantNumeric: "tabular-nums" }}>
                      {t.bpm ? t.bpm.toFixed(1) : "—"}
                    </span>
                  </div>
                  <div className="col-conf">
                    <ConfBar value={t.bpm_confidence} />
                  </div>
                  <div className="col-detector" style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {t.detector || "—"}
                  </div>
                  <div className="col-status">
                    <StatusBadge track={t} />
                  </div>
                  <div className="col-arrow" style={{ textAlign: "right", color: "var(--muted)" }}>
                    <ArrowIcon />
                  </div>
                </Link>
              );
            })
          )}
        </div>
      </div>

      {/* flexWrap: on phones the count + a many-page pagination can't share one
          line — without wrapping this row forced the whole page to scroll
          sideways (the only page-wide H-overflow in the mobile sweep). */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10, marginTop: 14, fontSize: 12, color: "var(--muted)" }}>
        <span>
          Showing <span style={{ color: "var(--text)", fontFamily: "var(--mono)" }}>{tracks.length}</span> of{" "}
          <span style={{ fontFamily: "var(--mono)" }}>{data?.total ?? 0}</span> total
        </span>
        {pages > 1 && (
          <div className="pagination" style={{ marginTop: 0 }}>
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
      </div>
    </>
  );
}
