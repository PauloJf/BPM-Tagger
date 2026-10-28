import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { basename, parentName } from "../lib/paths";
import type { Progress, TracksPage } from "../lib/types";
import { ConfBar, DecodeWarningBadge, FolderIcon, QueueButton, StatusBadge, trackSubtitle, trackTitle } from "../components/trackBits";
import TempoStrip, { tempoParams } from "../components/TempoStrip";
import { RatingStars, DislikeButton } from "../components/RatingStars";
import AddToPlaylistMenu from "../components/AddToPlaylistMenu";
import LibraryTabs from "../components/LibraryTabs";
import { QueueActions } from "../components/QueueActions";
import { ArtToggle, Cover, useArtwork } from "../components/Artwork";
import { useTitle } from "../hooks/useTitle";
import PageHeader from "../components/PageHeader";
import { usePlayer, type PlayerTrack } from "../lib/player";

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
  // "" (the unchanged default: newest analyzed first) → "plays" → "plays_asc" → "".
  const sort = params.get("sort") || "";

  // Local search text (debounced into the URL). The tempo inputs live in TempoStrip.
  const [searchText, setSearchText] = useState(q);
  // Mobile only: the secondary controls (per-page, artwork toggle) collapse
  // behind a "Filters" button; on desktop they're always inline.
  const [filtersOpen, setFiltersOpen] = useState(false);
  // The "More filters" popover (rating and library-health filters).
  const [moreOpen, setMoreOpen] = useState(false);
  const moreRef = useRef<HTMLDivElement>(null);

  // Keep the local search box in sync when the URL changes externally (e.g. Clear).
  useEffect(() => setSearchText(q), [q]);

  // Close the popover on an outside click or Escape.
  useEffect(() => {
    if (!moreOpen) return;
    const onDown = (e: MouseEvent) => {
      if (moreRef.current && !moreRef.current.contains(e.target as Node)) setMoreOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setMoreOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [moreOpen]);

  const debounce = useRef<number | undefined>(undefined);
  function scheduleApply(next: { q?: string }) {
    window.clearTimeout(debounce.current);
    debounce.current = window.setTimeout(() => {
      setParams((prev) => {
        const p = new URLSearchParams(prev);
        const qv = (next.q ?? searchText).trim();
        if (qv) p.set("q", qv);
        else p.delete("q");
        p.set("page", "1");
        return p;
      }, { replace: true });
    }, 300);
  }

  /** The tempo filter's one way into the URL: an empty BPM clears it. */
  function applyTempo(next: { bpm: string; tol: string }) {
    setParams((prev) => tempoParams(prev, next), { replace: true });
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

  /** Cycle the Plays column: most played → least played → back to the default
   *  order. Three states rather than two so the default is always one more click
   *  away, instead of being lost the moment you sort. */
  function toggleSort() {
    const next = sort === "plays" ? "plays_asc" : sort === "plays_asc" ? "" : "plays";
    setParams((prev) => {
      const p = new URLSearchParams(prev);
      if (next) p.set("sort", next);
      else p.delete("sort");
      p.set("page", "1");
      return p;
    });
  }

  /** Same cycle for the Rating column: highest first → lowest first → default. */
  function toggleRatingSort() {
    const next = sort === "rating" ? "rating_asc" : sort === "rating_asc" ? "" : "rating";
    setParams((prev) => {
      const p = new URLSearchParams(prev);
      if (next) p.set("sort", next);
      else p.delete("sort");
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

  // Same filters/sort the table is showing — every queue action (Play,
  // Shuffle, Add to queue) must act on what you see, not just the loaded page.
  async function fetchFilteredTracks(): Promise<PlayerTrack[]> {
    const sp = new URLSearchParams();
    if (q) sp.set("q", q);
    if (filter) sp.set("filter", filter);
    if (bpm) { sp.set("bpm", bpm); sp.set("bpm_tol", bpmTol); if (cadence) sp.set("bpm_cadence", "1"); }
    if (sort) sp.set("sort", sort);
    const res = await api.get<{ tracks: { file_path: string; title: string | null; artist: string | null;
      loudness_lufs: number | null; disliked?: number | null }[] }>(
      `/api/tracks/paths?${sp.toString()}`);
    // `disliked` (the admin's — this is an admin page) lets Shuffle skip them;
    // Play in order keeps them (D13).
    return res.tracks.map((t) => ({
      path: t.file_path, title: t.title?.trim() || basename(t.file_path), artist: t.artist || "",
      loudnessLufs: t.loudness_lufs, disliked: !!t.disliked,
    }));
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

  async function setRating(path: string, rating: number | null) {
    // Optimistic flip in the cache; the invalidate reconciles with the server.
    qc.setQueryData<TracksPage>(["tracks", search], (d) => d && {
      ...d,
      tracks: d.tracks.map((t) => (t.file_path === path ? { ...t, rating, starred: rating != null && rating >= 4 ? 1 : 0 } : t)),
    });
    try {
      await api.post("/api/track/rating", { path, rating });
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

  // The four filters people reach for sit on the toolbar; the rest (rating
  // thresholds and the library-health checks) live behind "More filters".
  interface Pill { key: string; label: string; count?: number }
  const primaryPills: Pill[] = [
    { key: "", label: "All", count: data?.all_count },
    { key: "starred", label: "Starred", count: data?.starred_count },
    { key: "unrated", label: "Unrated" },
    { key: "review", label: "Review", count: data?.review_count },
  ];
  const ratingPills: Pill[] = [
    { key: "rated1", label: "★1+" },
    { key: "rated2", label: "★2+" },
    { key: "rated3", label: "★3+" },
    { key: "rated4", label: "★4+" },
    { key: "rated5", label: "★5" },
    { key: "disliked", label: "Disliked", count: data?.disliked_count },
  ];
  const healthPills: Pill[] = [
    { key: "locked", label: "Locked", count: data?.locked_count },
    { key: "problems", label: "Problems", count: data?.problems_count },
    { key: "no_isrc", label: "No ISRC", count: data?.no_isrc_count },
    { key: "unplaylisted", label: "No playlist", count: data?.unplaylisted_count },
    { key: "deleted", label: "Deleted", count: data?.deleted_count },
  ];
  // When the active filter is one of the hidden ones, the More button says so.
  const hiddenActive = [...ratingPills, ...healthPills].find((p) => p.key === filter);

  function pillButton(pl: Pill) {
    return (
      <button
        key={pl.key}
        className={"filter-pill" + (filter === pl.key ? " active" : "")}
        aria-pressed={filter === pl.key}
        onClick={() => { setFilter(pl.key); setMoreOpen(false); }}
      >
        {pl.label}
        {pl.count != null && <span className="pill-count">{pl.count}</span>}
      </button>
    );
  }

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
        actions={
          <QueueActions
            getTracks={fetchFilteredTracks}
            empty={!data?.total}
            label=" all"
            disabledTitle="No tracks in this view"
          />
        }
      />

      <TempoStrip
        bpm={bpm}
        tol={bpmTol}
        cadence={cadence}
        total={data?.total}
        onApply={applyTempo}
        onCadence={setCadence}
      />

      <div className="lib-toolbar">
        <div className="lib-filter-row" ref={moreRef}>
          <div className="filter-pills">{primaryPills.map(pillButton)}</div>
          <button
            type="button"
            className={"filter-more-btn" + (hiddenActive ? " active" : "")}
            aria-expanded={moreOpen}
            aria-haspopup="true"
            onClick={() => setMoreOpen((o) => !o)}
          >
            {hiddenActive ? hiddenActive.label : "More filters"}
            {hiddenActive?.count != null && <span className="pill-count">{hiddenActive.count}</span>}
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M6 9l6 6 6-6" /></svg>
          </button>
          {moreOpen && (
            <div className="filter-more-panel" role="group" aria-label="More filters">
              <div className="filter-more-title">Rating</div>
              <div className="filter-more-chips">{ratingPills.map(pillButton)}</div>
              <div className="filter-more-title">Library health</div>
              <div className="filter-more-chips">{healthPills.map(pillButton)}</div>
            </div>
          )}
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
            title="Show display options"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" style={{ marginRight: 5 }}><path d="M4 6h16M7 12h10M10 18h4" /></svg>
            Options
          </button>
        </div>
        <div className={"lib-adv-filters" + (filtersOpen ? " open" : "")}>
          <ArtToggle show={showArt} onToggle={toggleArt} />
          {(q || bpm) && (
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => {
                setSearchText("");
                setParams(new URLSearchParams(), { replace: true });
              }}
            >
              Clear
            </button>
          )}
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
          <button
            type="button"
            className="tracks-sort-btn"
            aria-label="Sort by plays"
            aria-pressed={sort === "plays" || sort === "plays_asc"}
            title={sort === "plays" ? "Most played first — click for least played"
              : sort === "plays_asc" ? "Least played first — click to clear the sort"
              : "Sort by play count (most played first)"}
            onClick={toggleSort}
          >
            Plays{sort === "plays" ? " ↓" : sort === "plays_asc" ? " ↑" : ""}
          </button>
          <button
            type="button"
            className="tracks-sort-btn col-rating"
            aria-label="Sort by rating"
            aria-pressed={sort === "rating" || sort === "rating_asc"}
            title={sort === "rating" ? "Highest rated first — click for lowest first"
              : sort === "rating_asc" ? "Lowest rated first — click to clear the sort"
              : "Sort by rating (highest first)"}
            onClick={toggleRatingSort}
          >
            Rating{sort === "rating" ? " ↓" : sort === "rating_asc" ? " ↑" : ""}
          </button>
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
              const meta = { path: t.file_path, title, artist: t.artist || "", bpm: t.bpm,
                loudnessLufs: t.loudness_lufs };
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
                      {/* The folder only earns a line when there's no artist/album
                          to show instead (untagged files). */}
                      {folder && !subtitle && (
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
                  {/* Same order as the header: Plays, then Rating. */}
                  <div className="col-plays" style={{ textAlign: "right", fontFamily: "var(--mono)", fontSize: 13, color: t.play_count ? "var(--text)" : "var(--muted)", fontVariantNumeric: "tabular-nums" }}>
                    {t.play_count ? t.play_count : "–"}
                  </div>
                  <div
                    className="col-rating"
                    style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 2 }}
                    onClick={(e) => { e.preventDefault(); e.stopPropagation(); }}
                  >
                    <RatingStars value={t.rating ?? null} onChange={(v) => setRating(t.file_path, v)} compact size={13} label={title} />
                    <DislikeButton on={!!t.disliked} onToggle={() => toggleDislike(t.file_path, !!t.disliked)} size={13} />
                  </div>
                  <div className="col-status" style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                    <StatusBadge track={t} quietOk />
                    <DecodeWarningBadge track={t} />
                  </div>
                  {/* Secondary actions appear on hover/focus; the whole row is
                      already a link to the track page. */}
                  <div className="col-actions">
                    <QueueButton track={meta} extraBtn />
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
                    <AddToPlaylistMenu path={t.file_path} className="row-play row-extra-btn" title="Add to playlist" />
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
