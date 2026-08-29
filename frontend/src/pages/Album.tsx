import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, apiUpload } from "../lib/api";
import type { Track } from "../lib/types";
import { basename } from "../lib/paths";
import { usePlayer } from "../lib/player";
import { useTitle } from "../hooks/useTitle";
import { ArtToggle, Cover, useArtwork } from "../components/Artwork";
import { ArtistLinks } from "../components/ArtistLinks";
import { ImagePicker } from "../components/ImagePicker";
import { QueueActions } from "../components/QueueActions";
import { QueueButton } from "../components/trackBits";
import RelatedPanel from "../components/RelatedPanel";

interface AlbumResp {
  album: string;
  album_artist: string;
  tracks: Track[];
  stats: { tracks: number; avg_bpm: number | null; year: number | null };
}

const NextIcon = () => <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="5,4 13,12 5,20" /><rect x="14" y="4" width="2.5" height="16" rx="1" /></svg>;

export default function Album() {
  const [params] = useSearchParams();
  const album = params.get("album") || "";
  const albumArtist = params.get("album_artist") || "";
  useTitle(album || "Album");
  const player = usePlayer();
  const [showArt, toggleArt] = useArtwork();

  const q = useQuery({
    queryKey: ["album", album, albumArtist],
    queryFn: () => api.get<AlbumResp>(`/api/album?album=${encodeURIComponent(album)}&album_artist=${encodeURIComponent(albumArtist)}`),
    enabled: !!album,
  });

  const tracks = q.data?.tracks ?? [];
  const stats = q.data?.stats;
  const aa = q.data?.album_artist || albumArtist;
  const toPT = (t: Track) => ({ path: t.file_path, title: t.title || basename(t.file_path),
    artist: t.artist || "", bpm: t.bpm, loudnessLufs: t.loudness_lufs });

  const [pickerOpen, setPickerOpen] = useState(false);
  const [imgV, setImgV] = useState(0);
  const [coverMsg, setCoverMsg] = useState("");

  async function applyAlbumCover(pick: { url?: string; file?: File }) {
    let r: { ok: boolean; updated: number; failed: string[] };
    if (pick.url) {
      r = await api.post(`/api/album/cover`, { album, album_artist: aa, url: pick.url });
    } else if (pick.file) {
      r = await apiUpload(`/api/album/cover?album=${encodeURIComponent(album)}&album_artist=${encodeURIComponent(aa)}`, pick.file);
    } else return;
    setImgV(Date.now());
    setPickerOpen(false);
    setCoverMsg(r.failed.length
      ? `Cover set on ${r.updated} track${r.updated === 1 ? "" : "s"} — ${r.failed.length} failed`
      : `Cover set on ${r.updated} track${r.updated === 1 ? "" : "s"}`);
    setTimeout(() => setCoverMsg(""), 5000);
  }

  return (
    <>
      <div className="detail-header">
        {showArt && tracks.length > 0 && <Cover path={tracks[0].file_path} size={92} v={imgV} />}
        <div className="detail-header-text">
          <Link to="/albums" className="btn btn-bare btn-sm" style={{ paddingLeft: 0 }}>← Albums</Link>
          <h1 style={{ fontSize: 28, fontWeight: 600, letterSpacing: "-0.02em", margin: "6px 0 4px" }}>{album || "Album"}</h1>
          <p style={{ fontSize: 13, color: "var(--muted)" }}>
            <ArtistLinks artist={aa} linkStyle={{ color: "var(--accent-2)" }} />
            {stats ? `${aa ? " · " : ""}${stats.tracks} tracks${stats.year ? ` · ${stats.year}` : ""}${stats.avg_bpm != null ? ` · avg ${stats.avg_bpm} BPM` : ""}` : ""}
            {coverMsg && <span style={{ color: "var(--ok-fg)" }}> · {coverMsg}</span>}
          </p>
        </div>
        <div className="detail-header-actions">
          <ArtToggle show={showArt} onToggle={toggleArt} />
          <button className="btn btn-ghost btn-sm" disabled={!tracks.length} onClick={() => setPickerOpen(true)} title="Change the cover on every track of this album">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" style={{ marginRight: 4 }}>
              <path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
            </svg>
            Cover
          </button>
          <QueueActions tracks={tracks.map(toPT)} label=" all" disabledTitle="No tracks on this album" />
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
              <QueueButton track={toPT(t)} />
              <button className="row-play" aria-label="Play next" title="Play next" onClick={() => player.playNext(toPT(t))}><NextIcon /></button>
              <Link
                to={`/track?path=${encodeURIComponent(t.file_path)}&back=album&back_album=${encodeURIComponent(album)}&back_artist=${encodeURIComponent(aa)}`}
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

      {(() => {
        const seed = aa || tracks[0]?.artist || "";
        return seed ? <RelatedPanel artist={seed} context="album" /> : null;
      })()}

      {pickerOpen && (
        <ImagePicker
          kind="album"
          title={`Album cover — ${album}`}
          initialQuery={`${aa} ${album}`.trim()}
          onPick={applyAlbumCover}
          onClose={() => setPickerOpen(false)}
        />
      )}
    </>
  );
}
