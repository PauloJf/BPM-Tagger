import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useTitle } from "../hooks/useTitle";
import PageHeader from "../components/PageHeader";
import LibraryTabs from "../components/LibraryTabs";
import { ArtistImage, ArtToggle, useArtwork } from "../components/Artwork";

interface ArtistRow {
  name: string;
  tracks: number;
  albums: number;
  avg_bpm: number | null;
  sample_path: string;
}

export default function Artists() {
  useTitle("Artists");
  const [filter, setFilter] = useState("");
  const [showArt, toggleArt] = useArtwork();

  const q = useQuery({
    queryKey: ["artists"],
    queryFn: () => api.get<{ artists: ArtistRow[] }>("/api/artists"),
  });

  const all = q.data?.artists ?? [];
  const shown = useMemo(() => {
    const f = filter.trim().toLowerCase();
    return f ? all.filter((a) => a.name.toLowerCase().includes(f)) : all;
  }, [all, filter]);

  return (
    <>
      <PageHeader
        title="Artists"
        subtitle={<><span style={{ fontFamily: "var(--mono)", color: "var(--text)" }}>{all.length}</span> artists</>}
        tabs={<LibraryTabs />}
      />

      <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 10, marginBottom: 16, flexWrap: "wrap" }}>
        <ArtToggle show={showArt} onToggle={toggleArt} />
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
            placeholder="Filter artists…"
            className="search-input"
            autoComplete="off"
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
      </div>

      {q.isLoading ? (
        <div className="card" style={{ color: "var(--muted)" }}>Loading…</div>
      ) : shown.length === 0 ? (
        <div className="card" style={{ color: "var(--muted)" }}>
          {all.length === 0 ? "No artists yet — tag metadata shows up here after a scan." : "No artists match the filter."}
        </div>
      ) : (
        <div className="browse-grid">
          {shown.map((a) => (
            <Link key={a.name} to={`/artist?name=${encodeURIComponent(a.name)}`} className="browse-card">
              {showArt && <ArtistImage name={a.name} fallbackPath={a.sample_path} size={46} />}
              <span className="browse-card-text">
              <span className="browse-card-name" title={a.name}>{a.name}</span>
              <span className="browse-card-meta">
                {a.tracks} track{a.tracks === 1 ? "" : "s"} · {a.albums} album{a.albums === 1 ? "" : "s"}
              </span>
              <span className="browse-card-bpm">{a.avg_bpm != null ? `avg ${a.avg_bpm} BPM` : " "}</span>
              </span>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}
