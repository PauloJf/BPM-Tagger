import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useTitle } from "../hooks/useTitle";
import PageHeader from "../components/PageHeader";
import EmptyState from "../components/EmptyState";

interface DupGroup {
  artist: string;
  title: string;
  count: number;
  tracks: { file_path: string; title: string | null; artist: string | null; album: string | null; bpm: number | null; managed: number; isrc?: string | null; duration_ms?: number | null }[];
}

export default function Duplicates() {
  useTitle("Duplicates");
  const navigate = useNavigate();
  const dupQ = useQuery({ queryKey: ["duplicates"], queryFn: () => api.get<{ groups: DupGroup[] }>("/api/duplicates") });
  const groups = dupQ.data?.groups ?? [];

  return (
    <>
      <PageHeader
        title="Duplicates"
        subtitle={
          groups.length > 0
            ? <><span style={{ fontFamily: "var(--mono)", color: "var(--text)" }}>{groups.length}</span> group{groups.length === 1 ? "" : "s"} — same normalized artist + title, or a shared ISRC</>
            : "Likely copies of the same recording — same normalized artist + title, or a shared ISRC."
        }
      />

      {groups.length === 0 ? (
        <EmptyState message={dupQ.isLoading ? "Loading…" : "No duplicates found."} />
      ) : (
        <div className="card">
          <div className="section-label">
            <span>{groups.length} group{groups.length === 1 ? "" : "s"}</span>
            <span className="section-hint">same normalized artist + title, or shared ISRC</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {groups.map((g, i) => (
              <div key={i}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4, flexWrap: "wrap" }}>
                  <div style={{ fontSize: 13, fontWeight: 500, minWidth: 0 }}>
                    {g.tracks[0]?.artist || g.artist} – {g.tracks[0]?.title || g.title}
                    <span className="chip chip--warn" style={{ marginLeft: 8 }}>{g.count}</span>
                  </div>
                  <div style={{ flex: 1 }} />
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => navigate(`/compare?${g.tracks.map((t) => `path=${encodeURIComponent(t.file_path)}`).join("&")}`)}
                  >
                    Compare
                  </button>
                </div>
                {g.tracks.map((t) => (
                  <div key={t.file_path} style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {t.file_path}{t.bpm ? ` · ${t.bpm.toFixed(1)} BPM` : ""}{t.isrc ? ` · ${t.isrc}` : ""}{t.managed ? " · managed" : ""}
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
