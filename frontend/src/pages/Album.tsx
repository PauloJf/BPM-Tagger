import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Track } from "../lib/types";
import { basename } from "../lib/paths";
import { usePlayer } from "../lib/player";
import { useTitle } from "../hooks/useTitle";

interface AlbumResp {
  album: string;
  album_artist: string;
  tracks: Track[];
  stats: { tracks: number; avg_bpm: number | null; year: number | null };
}

const AddIcon = () => <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"><path d="M12 5v14M5 12h14" /></svg>;
const NextIcon = () => <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="5,4 13,12 5,20" /><rect x="14" y="4" width="2.5" height="16" rx="1" /></svg>;

export default function Album() {
  const [params] = useSearchParams();
  const album = params.get("album") || "";
  const albumArtist = params.get("album_artist") || "";
  useTitle(album || "Album");
  const player = usePlayer();

  const q = useQuery({
    queryKey: ["album", album, albumArtist],
    queryFn: () => api.get<AlbumResp>(`/api/album?album=${encodeURIComponent(album)}&album_artist=${encodeURIComponent(albumArtist)}`),
    enabled: !!album,
  });

  const tracks = q.data?.tracks ?? [];
  const stats = q.data?.stats;
  const aa = q.data?.album_artist || albumArtist;
  const toPT = (t: Track) => ({ path: t.file_path, title: t.title || basename(t.file_path), artist: t.artist || "" });
  const playAll = (shuffle: boolean) => { if (tracks.length) player.playQueue(tracks.map(toPT), 0, { shuffle }); };

  return (
    <>
      <div style={{ marginBottom: 20, display: "flex", alignItems: "flex-end", gap: 16, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Link to="/stats" className="btn btn-bare btn-sm" style={{ paddingLeft: 0 }}>← Stats</Link>
          <h1 style={{ fontSize: 28, fontWeight: 600, letterSpacing: "-0.02em", margin: "6px 0 4px" }}>{album || "Album"}</h1>
          <p style={{ fontSize: 13, color: "var(--muted)" }}>
            {aa && <Link to={`/artist?name=${encodeURIComponent(aa)}`} style={{ color: "var(--accent-2)" }}>{aa}</Link>}
            {stats ? `${aa ? " · " : ""}${stats.tracks} tracks${stats.year ? ` · ${stats.year}` : ""}${stats.avg_bpm != null ? ` · avg ${stats.avg_bpm} BPM` : ""}` : ""}
          </p>
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
        <div className="card" style={{ color: "var(--muted)" }}>No tracks found for this album.</div>
      ) : (
        <div className="card" style={{ padding: 4 }}>
          {tracks.map((t) => (
            <div key={t.file_path} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 10px", borderRadius: 8 }}>
              <span style={{ width: 22, textAlign: "right", fontFamily: "var(--mono)", fontSize: 12, color: "var(--muted)", flexShrink: 0 }}>{t.track_no ?? "–"}</span>
              <button className="row-play" aria-label="Play" onClick={() => player.play(toPT(t))}>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="6,4 20,12 6,20" /></svg>
              </button>
              <button className="row-play" aria-label="Add to queue" title="Add to queue" onClick={() => player.enqueue(toPT(t))}><AddIcon /></button>
              <button className="row-play" aria-label="Play next" title="Play next" onClick={() => player.playNext(toPT(t))}><NextIcon /></button>
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
      )}
    </>
  );
}
