import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Track } from "../lib/types";
import { basename } from "../lib/paths";
import { usePlayer } from "../lib/player";
import { useTitle } from "../hooks/useTitle";

interface ArtistResp {
  name: string;
  tracks: Track[];
  stats: { tracks: number; albums: number; avg_bpm: number | null; min_bpm: number | null; max_bpm: number | null };
}

export default function Artist() {
  const [params] = useSearchParams();
  const name = params.get("name") || "";
  useTitle(name || "Artist");
  const player = usePlayer();

  const q = useQuery({
    queryKey: ["artist", name],
    queryFn: () => api.get<ArtistResp>(`/api/artist?name=${encodeURIComponent(name)}`),
    enabled: !!name,
  });

  const tracks = q.data?.tracks ?? [];
  const stats = q.data?.stats;
  const toPT = (t: Track) => ({ path: t.file_path, title: t.title || basename(t.file_path), artist: t.artist || "" });
  const playAll = (shuffle: boolean) => { if (tracks.length) player.playQueue(tracks.map(toPT), 0, { shuffle }); };

  // Group by album, preserving the album-ordered sequence from the API.
  const albums: { album: string; tracks: Track[] }[] = [];
  for (const t of tracks) {
    const a = t.album || "Unknown album";
    let g = albums.find((x) => x.album === a);
    if (!g) { g = { album: a, tracks: [] }; albums.push(g); }
    g.tracks.push(t);
  }

  return (
    <>
      <div style={{ marginBottom: 20, display: "flex", alignItems: "flex-end", gap: 16, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Link to="/tracks" className="btn btn-bare btn-sm" style={{ paddingLeft: 0 }}>← Library</Link>
          <h1 style={{ fontSize: 28, fontWeight: 600, letterSpacing: "-0.02em", margin: "6px 0 4px" }}>{name || "Artist"}</h1>
          {stats && (
            <p style={{ fontSize: 13, color: "var(--muted)" }}>
              {stats.tracks} tracks · {stats.albums} album{stats.albums === 1 ? "" : "s"}
              {stats.avg_bpm != null ? ` · avg ${stats.avg_bpm} BPM` : ""}
              {stats.min_bpm != null ? ` · range ${stats.min_bpm}–${stats.max_bpm}` : ""}
            </p>
          )}
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn btn-primary btn-sm" disabled={!tracks.length} onClick={() => playAll(false)}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" style={{ marginRight: 4 }}><polygon points="6,4 20,12 6,20" /></svg>
            Play all
          </button>
          <button className="btn btn-ghost btn-sm" disabled={!tracks.length} onClick={() => playAll(true)}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ marginRight: 4 }}><path d="M16 3h5v5M4 20L21 3M21 16v5h-5M15 15l6 6M4 4l5 5" /></svg>
            Shuffle
          </button>
        </div>
      </div>

      {q.isLoading ? (
        <div className="card" style={{ color: "var(--muted)" }}>Loading…</div>
      ) : tracks.length === 0 ? (
        <div className="card" style={{ color: "var(--muted)" }}>No tracks found for this artist.</div>
      ) : (
        albums.map((g) => (
          <div key={g.album} style={{ marginBottom: 18 }}>
            <div className="section-label"><span>{g.album}</span></div>
            <div className="card" style={{ padding: 4 }}>
              {g.tracks.map((t) => (
                <div key={t.file_path} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 10px", borderRadius: 8 }}>
                  <button className="row-play" aria-label="Play" onClick={() => player.play(toPT(t))}>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="6,4 20,12 6,20" /></svg>
                  </button>
                  <button className="row-play" aria-label="Add to queue" title="Add to queue" onClick={() => player.enqueue(toPT(t))}>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"><path d="M12 5v14M5 12h14" /></svg>
                  </button>
                  <button className="row-play" aria-label="Play next" title="Play next" onClick={() => player.playNext(toPT(t))}>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="5,4 13,12 5,20" /><rect x="14" y="4" width="2.5" height="16" rx="1" /></svg>
                  </button>
                  <Link
                    to={`/track?path=${encodeURIComponent(t.file_path)}`}
                    style={{ flex: 1, minWidth: 0, color: "var(--text)", textDecoration: "none", fontSize: 13, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                    title={t.title || basename(t.file_path)}
                  >
                    {t.title || basename(t.file_path)}
                  </Link>
                  <span style={{ fontFamily: "var(--mono)", fontSize: 13, fontVariantNumeric: "tabular-nums", color: t.bpm ? "var(--text)" : "var(--muted)", flexShrink: 0 }}>
                    {t.bpm ? t.bpm.toFixed(1) : "—"}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ))
      )}
    </>
  );
}
