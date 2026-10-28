import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useTitle } from "../hooks/useTitle";
import PageHeader from "../components/PageHeader";
import LibraryTabs from "../components/LibraryTabs";
import TempoStrip, { tempoParams } from "../components/TempoStrip";
import { ArtToggle, Cover, useArtwork } from "../components/Artwork";

export interface AlbumRow {
  album: string;
  album_artist: string;
  tracks: number;
  year: number | null;
  avg_bpm: number | null;
  sample_path: string;
  /** Tracks matching the tempo filter; only present while one is set. */
  fit?: number;
  /** Of those, the ones that only match at half or double time. */
  fit_folded?: number;
}

type SortKey = "fit" | "az" | "tracks";

/** What the cover badge says. With a tempo set: how many tracks fit, or "half-time"
 *  when every match is only reached by folding; otherwise the album's average BPM. */
export function albumBadge(a: AlbumRow, active: boolean): { text: string; hint: string } | null {
  if (active && a.fit != null) {
    const folded = a.fit_folded ?? 0;
    if (a.fit > 0 && folded === a.fit) {
      return { text: `${a.fit} half-time`, hint: `${a.fit} of ${a.tracks} tracks fit at half or double time` };
    }
    return {
      text: `${a.fit} of ${a.tracks} fit`,
      hint: folded > 0 ? `${a.fit} of ${a.tracks} tracks fit (${folded} at half or double time)` : `${a.fit} of ${a.tracks} tracks fit`,
    };
  }
  return a.avg_bpm != null ? { text: `avg ${a.avg_bpm} BPM`, hint: "Average BPM" } : null;
}

export default function Albums() {
  useTitle("Albums");
  const [params, setParams] = useSearchParams();
  const [filter, setFilter] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [sortChoice, setSortChoice] = useState<SortKey | null>(null);
  const [showArt, toggleArt] = useArtwork();

  const bpm = params.get("bpm") || "";
  const bpmTol = params.get("bpm_tol") || "5";
  const cadence = params.get("bpm_cadence") === "1";
  const active = Number(bpm) > 0;

  const qs = new URLSearchParams();
  if (active) {
    qs.set("bpm", bpm);
    qs.set("bpm_tol", bpmTol);
    if (cadence) qs.set("bpm_cadence", "1");
  }
  const search = qs.toString();
  const q = useQuery({
    queryKey: ["albums", search],
    queryFn: () => api.get<{ albums: AlbumRow[] }>(`/api/albums${search ? `?${search}` : ""}`),
    placeholderData: (prev) => prev,
  });

  const all = q.data?.albums ?? [];

  // With a tempo set the useful default is "which albums have something for this
  // pace", best fit first; without one it's the plain A–Z index.
  const sort: SortKey = sortChoice === "fit" && !active ? "az" : sortChoice ?? (active ? "fit" : "az");
  const onlyFitting = active && !showAll;

  const shown = useMemo(() => {
    const f = filter.trim().toLowerCase();
    let rows = all;
    if (f) rows = rows.filter((a) => a.album.toLowerCase().includes(f) || a.album_artist.toLowerCase().includes(f));
    if (onlyFitting) rows = rows.filter((a) => (a.fit ?? 0) > 0);
    const byName = (a: AlbumRow, b: AlbumRow) => a.album.localeCompare(b.album, undefined, { sensitivity: "base" });
    return [...rows].sort((a, b) => {
      if (sort === "fit") return (b.fit ?? 0) - (a.fit ?? 0) || byName(a, b);
      if (sort === "tracks") return b.tracks - a.tracks || byName(a, b);
      return byName(a, b);
    });
  }, [all, filter, onlyFitting, sort]);

  const fitting = active ? all.filter((a) => (a.fit ?? 0) > 0).length : 0;

  return (
    <>
      <PageHeader
        title="Albums"
        subtitle={<><span style={{ fontFamily: "var(--mono)", color: "var(--text)" }}>{all.length}</span> albums</>}
        tabs={<LibraryTabs />}
      />

      <TempoStrip
        bpm={bpm}
        tol={bpmTol}
        cadence={cadence}
        total={undefined}
        fitLabel={`${fitting.toLocaleString()} ${fitting === 1 ? "album" : "albums"} with tracks that fit`}
        onApply={(next) => setParams((prev) => tempoParams(prev, next), { replace: true })}
        onCadence={(on) => setParams((prev) => {
          const p = new URLSearchParams(prev);
          if (on) p.set("bpm_cadence", "1");
          else p.delete("bpm_cadence");
          return p;
        }, { replace: true })}
      />

      <div className="lib-toolbar">
        {active && (
          <div className="filter-pills">
            <button className={"filter-pill" + (!showAll ? " active" : "")} aria-pressed={!showAll} onClick={() => setShowAll(false)}>
              With tracks that fit<span className="pill-count">{fitting}</span>
            </button>
            <button className={"filter-pill" + (showAll ? " active" : "")} aria-pressed={showAll} onClick={() => setShowAll(true)}>
              All albums<span className="pill-count">{all.length}</span>
            </button>
          </div>
        )}
        <div className="lib-search-row artist-tools">
          <ArtToggle show={showArt} onToggle={toggleArt} />
          <label className="artist-sort">
            Sort
            <select value={sort} onChange={(e) => setSortChoice(e.target.value as SortKey)} aria-label="Sort albums">
              {active && <option value="fit">Most tracks that fit</option>}
              <option value="az">A–Z</option>
              <option value="tracks">Most tracks</option>
            </select>
          </label>
          <div className="search-wrap">
            <span className="search-icon">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
                <circle cx="11" cy="11" r="6" />
                <path d="M16 16 L21 21" />
              </svg>
            </span>
            <input
              type="text"
              value={filter}
              placeholder="Search albums…"
              className="search-input"
              autoComplete="off"
              onChange={(e) => setFilter(e.target.value)}
            />
          </div>
        </div>
      </div>

      {q.isLoading ? (
        <div className="card" style={{ color: "var(--muted)" }}>Loading…</div>
      ) : shown.length === 0 ? (
        <div className="card" style={{ color: "var(--muted)" }}>
          {all.length === 0
            ? "No albums yet — tag metadata shows up here after a scan."
            : onlyFitting && !filter.trim()
              ? "No album has tracks at this tempo. Widen the range, include half / double time, or pick Any."
              : "No albums match."}
        </div>
      ) : (
        <>
          <div className="album-grid">
            {shown.map((a) => {
              const badge = albumBadge(a, active);
              const pct = active && a.fit != null && a.tracks > 0 ? Math.min(100, Math.round((a.fit / a.tracks) * 100)) : 0;
              return (
                <Link
                  key={`${a.album} ${a.album_artist}`}
                  to={`/album?album=${encodeURIComponent(a.album)}&album_artist=${encodeURIComponent(a.album_artist)}`}
                  className="album-card"
                >
                  <span className="album-cover">
                    {showArt
                      ? <Cover path={a.sample_path} size={200} style={{ width: "100%", height: "100%" }} />
                      : <span className="album-cover-blank" aria-hidden="true">♪</span>}
                    {badge && <span className="album-badge" title={badge.hint}>{badge.text}</span>}
                  </span>
                  <span className="album-title" title={a.album}>{a.album}</span>
                  <span className="album-sub" title={a.album_artist}>
                    {a.album_artist || "Unknown artist"}{a.year ? ` · ${a.year}` : ""}
                  </span>
                  <span className="album-foot">
                    {active && <span className="album-bar" aria-hidden="true"><span style={{ width: `${pct}%` }} /></span>}
                    <span>{a.tracks} track{a.tracks === 1 ? "" : "s"}</span>
                  </span>
                </Link>
              );
            })}
          </div>
          <div className="artist-foot">Showing {shown.length.toLocaleString()} of {all.length.toLocaleString()} albums</div>
        </>
      )}
    </>
  );
}
