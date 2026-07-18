import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import { useTitle } from "../hooks/useTitle";
import { useGrabberStatus } from "../hooks/useGrabberStatus";
import PageHeader from "../components/PageHeader";
import GrabberGate from "../components/GrabberGate";

interface SearchResult {
  spotify_track_id: string;
  title: string;
  artist: string;
  album: string;
  album_artist: string;
  duration_ms: number | null;
  isrc: string;
  track_no: number | null;
  disc_no: number | null;
  year: number | null;
  cover_url: string;
  in_library?: boolean;
  library_path?: string;   // matched library file — the "in library" chip links to it
  queued?: boolean;
}

export default function Search() {
  useTitle("Search");
  const qc = useQueryClient();
  const status = useGrabberStatus();
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");

  const searchQ = useQuery({
    queryKey: ["spotify-search", query],
    queryFn: () => api.get<{ results: SearchResult[] }>(`/api/spotify/search?q=${encodeURIComponent(query)}`),
    enabled: status.data?.enabled === true && !!query,
  });

  const add = useMutation({
    mutationFn: (r: SearchResult) => api.post("/api/queue", {
      spotify_track_id: r.spotify_track_id, title: r.title, artist: r.artist, album: r.album,
      album_artist: r.album_artist, duration_ms: r.duration_ms, isrc: r.isrc,
      track_no: r.track_no, disc_no: r.disc_no, year: r.year, cover_url: r.cover_url,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["spotify-search"] });
      qc.invalidateQueries({ queryKey: ["queue"] });
      qc.invalidateQueries({ queryKey: ["grabber-status"] });
    },
  });

  const connected = status.data?.spotify?.connected;
  const results = searchQ.data?.results ?? [];

  return (
    <GrabberGate title="Search & grab" subtitle="Search Spotify's catalog and queue a track for download.">
      <PageHeader title="Search & grab" subtitle="Search Spotify's catalog and queue a track for download." />

      {!connected ? (
        <div className="flash" style={{ background: "var(--warn-bg)", borderColor: "var(--warn-bd)", color: "var(--warn-fg)" }}>
          Spotify isn't connected. <Link to="/settings" style={{ color: "inherit", textDecoration: "underline" }}>Connect it in Settings</Link>.
        </div>
      ) : (
        <div style={{ display: "flex", gap: 8, marginBottom: 18, flexWrap: "wrap" }}>
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="artist – title"
            style={{ flex: 1, minWidth: 240 }}
            onKeyDown={(e) => { if (e.key === "Enter" && input.trim()) setQuery(input.trim()); }}
          />
          <button className="btn btn-primary btn-md" disabled={!input.trim()} onClick={() => setQuery(input.trim())}>Search</button>
        </div>
      )}

      {searchQ.isError && (
        <div className="flash error">{searchQ.error instanceof ApiError ? searchQ.error.message : "Search failed"}</div>
      )}

      <div className="tracks-table">
        {results.length === 0 ? (
          <div className="tracks-row-empty">{searchQ.isFetching ? "Searching…" : query ? "No results." : "Enter a search above."}</div>
        ) : (
          results.map((r) => (
            <div key={r.spotify_track_id} className="pl-track-row" style={{ gridTemplateColumns: "1fr auto" }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.title}</div>
                <div style={{ fontSize: 11, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {r.artist}{r.album ? ` · ${r.album}` : ""}{r.year ? ` · ${r.year}` : ""}
                </div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                {r.in_library ? (
                  // Link to the matched library track (same pattern as the
                  // Suggestions page's chip); plain chip if the path is absent.
                  r.library_path ? (
                    <Link className="chip chip--have" to={`/track?path=${encodeURIComponent(r.library_path)}`} title="Open the matching library track">✓ in library</Link>
                  ) : (
                    <span className="chip chip--have">✓ in library</span>
                  )
                ) : r.queued ? (
                  <span className="chip chip--queued">↓ queued</span>
                ) : (
                  <button className="btn btn-soft btn-sm" disabled={add.isPending} onClick={() => add.mutate(r)}>
                    Add to queue
                  </button>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </GrabberGate>
  );
}
