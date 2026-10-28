import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useTitle } from "../hooks/useTitle";
import PageHeader from "../components/PageHeader";
import LibraryTabs from "../components/LibraryTabs";
import TempoStrip, { tempoParams, windowReach } from "../components/TempoStrip";
import { ArtistImage, ArtToggle, useArtwork } from "../components/Artwork";

export interface ArtistRow {
  name: string;
  tracks: number;
  albums: number;
  avg_bpm: number | null;
  sample_path: string;
  /** Track counts per tempo bin across `mix_range`. */
  mix: number[];
  /** Tracks matching the tempo filter; only present while one is set. */
  fit?: number;
}

interface ArtistsResponse {
  artists: ArtistRow[];
  mix_range: [number, number];
}

type SortKey = "fit" | "az" | "tracks";

/** The A–Z rail's letter for a name: its first letter, or # for anything else. */
export function letterOf(name: string): string {
  const c = (name.trim()[0] ?? "").toUpperCase();
  return /[A-Z]/.test(c) ? c : "#";
}

const LETTERS = [..."ABCDEFGHIJKLMNOPQRSTUVWXYZ", "#"];

function initials(name: string): string {
  const words = name.split(/[\s+&]+/).filter((w) => /[\p{L}\p{N}]/u.test(w));
  return words.slice(0, 2).map((w) => [...w][0].toUpperCase()).join("") || "♪";
}

/** One artist's tempo mix: 16 small bars, lit where the tempo window falls. */
function TempoMix({ row, range, window: w, active }: {
  row: ArtistRow;
  range: [number, number];
  window: { target: number; tol: number; cadence: boolean };
  active: boolean;
}) {
  const bins = row.mix.length || 1;
  const width = (range[1] - range[0]) / bins;
  const peak = Math.max(1, ...row.mix);
  const label = active && row.fit != null
    ? `Tempo mix: ${row.fit} of ${row.tracks} tracks fit`
    : `Tempo mix across ${range[0]}–${range[1]} BPM`;
  return (
    <div className="artist-mix" role="img" aria-label={label}>
      {row.mix.map((n, i) => {
        const lo = range[0] + i * width;
        const reach = active ? windowReach(lo, lo + width, w) : 1;
        return (
          <span
            key={i}
            data-reach={active ? reach : "off"}
            style={{ height: n ? `${Math.max(12, Math.round((n / peak) * 100))}%` : "8%" }}
            data-empty={n === 0}
          />
        );
      })}
    </div>
  );
}

export default function Artists() {
  useTitle("Artists");
  const [params, setParams] = useSearchParams();
  const [filter, setFilter] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [sortChoice, setSortChoice] = useState<SortKey | null>(null);
  const [showArt, toggleArt] = useArtwork();

  const bpm = params.get("bpm") || "";
  const bpmTol = params.get("bpm_tol") || "5";
  const cadence = params.get("bpm_cadence") === "1";
  const active = Number(bpm) > 0;
  const win = { target: Number(bpm) || 0, tol: Number(bpmTol) || 0, cadence };

  const qs = new URLSearchParams();
  if (active) {
    qs.set("bpm", bpm);
    qs.set("bpm_tol", bpmTol);
    if (cadence) qs.set("bpm_cadence", "1");
  }
  const search = qs.toString();
  const q = useQuery({
    queryKey: ["artists", search],
    queryFn: () => api.get<ArtistsResponse>(`/api/artists${search ? `?${search}` : ""}`),
    placeholderData: (prev) => prev,
  });

  const all = q.data?.artists ?? [];
  const range: [number, number] = q.data?.mix_range ?? [70, 190];

  // With a tempo set the useful default is "who has something for this pace",
  // best fit first; without one it's the plain A–Z index.
  const sort: SortKey = sortChoice === "fit" && !active ? "az" : sortChoice ?? (active ? "fit" : "az");
  const onlyFitting = active && !showAll;

  const shown = useMemo(() => {
    const f = filter.trim().toLowerCase();
    let rows = all;
    if (f) rows = rows.filter((a) => a.name.toLowerCase().includes(f));
    if (onlyFitting) rows = rows.filter((a) => (a.fit ?? 0) > 0);
    const byName = (a: ArtistRow, b: ArtistRow) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
    return [...rows].sort((a, b) => {
      if (sort === "fit") return (b.fit ?? 0) - (a.fit ?? 0) || byName(a, b);
      if (sort === "tracks") return b.tracks - a.tracks || byName(a, b);
      return byName(a, b);
    });
  }, [all, filter, onlyFitting, sort]);

  const fitting = active ? all.filter((a) => (a.fit ?? 0) > 0).length : 0;
  const firstOfLetter = new Map<string, number>();
  shown.forEach((a, i) => {
    const l = letterOf(a.name);
    if (!firstOfLetter.has(l)) firstOfLetter.set(l, i);
  });
  const rail = sort === "az" && shown.length > 20;

  function jump(letter: string) {
    document.getElementById(`artist-letter-${letter}`)?.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  return (
    <>
      <PageHeader
        title="Artists"
        subtitle={<><span style={{ fontFamily: "var(--mono)", color: "var(--text)" }}>{all.length}</span> artists</>}
        tabs={<LibraryTabs />}
      />

      <TempoStrip
        bpm={bpm}
        tol={bpmTol}
        cadence={cadence}
        total={undefined}
        fitLabel={`${fitting.toLocaleString()} ${fitting === 1 ? "artist" : "artists"} with tracks that fit`}
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
              All artists<span className="pill-count">{all.length}</span>
            </button>
          </div>
        )}
        <div className="lib-search-row artist-tools">
          <ArtToggle show={showArt} onToggle={toggleArt} />
          <label className="artist-sort">
            Sort
            <select value={sort} onChange={(e) => setSortChoice(e.target.value as SortKey)} aria-label="Sort artists">
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
              placeholder="Search artists…"
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
            ? "No artists yet — tag metadata shows up here after a scan."
            : onlyFitting && !filter.trim()
              ? "No artist has tracks at this tempo. Widen the range, include half / double time, or pick Any."
              : "No artists match."}
        </div>
      ) : (
        <div className="artist-wrap">
          <div className="artist-list">
            <div className="artist-head" aria-hidden="true">
              <span />
              <span>Artist</span>
              <span className="artist-head-mix">Tempo mix</span>
              <span style={{ textAlign: "right" }}>{active ? "Fit" : "Avg BPM"}</span>
            </div>
            {shown.map((a, i) => {
              const letter = letterOf(a.name);
              const isFirst = firstOfLetter.get(letter) === i;
              return (
                <Link
                  key={a.name}
                  id={isFirst ? `artist-letter-${letter}` : undefined}
                  to={`/artist?name=${encodeURIComponent(a.name)}`}
                  className="artist-row"
                >
                  {showArt
                    ? <ArtistImage name={a.name} fallbackPath={a.sample_path} size={44} />
                    : <span className="artist-avatar" aria-hidden="true">{initials(a.name)}</span>}
                  <span className="artist-name-cell">
                    <span className="artist-name" title={a.name}>{a.name}</span>
                    <span className="artist-meta">
                      {a.albums} album{a.albums === 1 ? "" : "s"} · {a.tracks} track{a.tracks === 1 ? "" : "s"}
                    </span>
                  </span>
                  <TempoMix row={a} range={range} window={win} active={active} />
                  <span className="artist-fit">
                    {active && a.fit != null
                      ? <><b>{a.fit}</b><small> of {a.tracks}</small></>
                      : <b className="artist-fit--muted">{a.avg_bpm != null ? a.avg_bpm : "—"}</b>}
                  </span>
                </Link>
              );
            })}
            <div className="artist-foot">
              Showing {shown.length.toLocaleString()} of {all.length.toLocaleString()} artists
            </div>
          </div>
          {rail && (
            <nav className="az-rail" aria-label="Jump to letter">
              {LETTERS.map((l) => (
                <button key={l} type="button" disabled={!firstOfLetter.has(l)} onClick={() => jump(l)}>{l}</button>
              ))}
            </nav>
          )}
        </div>
      )}
    </>
  );
}
