import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { DeezerAlbumDetail, DeezerAlbumMeta, DeezerArtistResponse, RelatedTrack } from "../lib/types";
import { PreviewButton } from "./trackBits";

function TrackRow({ t }: { t: RelatedTrack }) {
  return (
    <div className="pl-track-row" style={{ gridTemplateColumns: "1fr auto", gap: 8 }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.title}</div>
        {t.album && <div style={{ fontSize: 11, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.album}</div>}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        {(t.preview_url || (t.in_library && t.file_path)) && (
          <PreviewButton
            track={{ dz_track_id: t.dz_track_id, title: t.title, artist: t.artist, preview_url: t.preview_url }}
            libraryPath={t.in_library && t.file_path ? t.file_path : undefined}
          />
        )}
        {t.in_library ? (
          t.file_path ? (
            <Link className="chip chip--have sugg-action" to={`/track?path=${encodeURIComponent(t.file_path)}`}>✓ in library</Link>
          ) : (
            <span className="chip chip--have sugg-action">✓ in library</span>
          )
        ) : null}
      </div>
    </div>
  );
}

function AlbumRow({ al }: { al: DeezerAlbumMeta }) {
  const [open, setOpen] = useState(false);
  const detailQ = useQuery({
    queryKey: ["deezer-album", al.dz_album_id],
    queryFn: () => api.get<{ album: DeezerAlbumDetail }>(`/api/deezer/album/${al.dz_album_id}`),
    enabled: open,
    staleTime: 60_000,
  });
  // Deezer's artist-albums list omits the track count (it's only on the full
  // album object), so it arrives as 0. Show the real count once expanded.
  const nbTracks = detailQ.data?.album.tracks.length ?? (al.nb_tracks || 0);

  return (
    <div style={{ borderBottom: "1px solid var(--border)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 4px" }}>
        {al.cover_url ? (
          <img src={al.cover_url} alt="" loading="lazy" className="art-thumb" style={{ width: 42, height: 42, flexShrink: 0 }} />
        ) : (
          <div className="art-thumb" style={{ width: 42, height: 42, display: "grid", placeItems: "center", flexShrink: 0 }} aria-hidden>♪</div>
        )}
        <button
          onClick={() => setOpen((o) => !o)}
          style={{ flex: 1, minWidth: 0, background: "none", border: "none", padding: 0, textAlign: "left", cursor: "pointer", color: "inherit" }}
          title="Show tracks"
        >
          <div style={{ fontSize: 13, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{al.title}</div>
          <div style={{ fontSize: 11, color: "var(--muted)" }}>
            {[al.year, nbTracks > 0 ? `${nbTracks} track${nbTracks === 1 ? "" : "s"}` : "", al.explicit ? "explicit" : ""].filter(Boolean).join(" · ")}
          </div>
        </button>
        <button className="btn btn-bare btn-sm" style={{ padding: "2px 8px" }} aria-expanded={open} onClick={() => setOpen((o) => !o)} title={open ? "Hide tracks" : "Show tracks"}>
          {open ? "–" : "+"}
        </button>
      </div>
      {open && (
        <div style={{ padding: "0 4px 6px" }}>
          {detailQ.isLoading ? (
            <div className="tracks-row-empty">Loading tracks…</div>
          ) : (detailQ.data?.album.tracks?.length ?? 0) === 0 ? (
            <div className="tracks-row-empty">No tracks found.</div>
          ) : (
            detailQ.data!.album.tracks.map((t) => (
              <TrackRow key={t.dz_track_id} t={t} />
            ))
          )}
        </div>
      )}
    </div>
  );
}

/** Full-detail popup for a Deezer artist: bio, top tracks (with 30 s previews), and
 *  discography (albums + singles/EPs). Opened from a related-artist row or the
 *  artist page's Browse Deezer button. */
export default function ArtistModal({ dzId, name, onClose }: { dzId: string; name: string; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const q = useQuery({
    queryKey: ["deezer-artist", dzId],
    queryFn: () => api.get<DeezerArtistResponse>(`/api/deezer/artist/${dzId}`),
    staleTime: 60_000,
  });
  const bioQ = useQuery({
    queryKey: ["related-description", name],
    queryFn: () => api.get<{ description: string }>(`/api/related/description?name=${encodeURIComponent(name)}`),
    staleTime: Infinity,
  });

  const artist = q.data?.artist;
  const albums = q.data?.albums ?? [];
  const singles = q.data?.singles ?? [];
  const topTracks = q.data?.top_tracks ?? [];

  return (
    <div
      role="dialog"
      aria-label={`${name} — artist`}
      onClick={onClose}
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)", zIndex: 200, display: "grid", placeItems: "center", padding: 16 }}
    >
      <div className="card" onClick={(e) => e.stopPropagation()} style={{ width: "min(720px, 100%)", maxHeight: "88vh", overflowY: "auto" }}>
        {/* Header */}
        <div style={{ display: "flex", gap: 14, alignItems: "flex-start", marginBottom: 16 }}>
          {(artist?.image_url) ? (
            <img src={artist.image_url} alt="" className="art-thumb art-thumb--round" style={{ width: 72, height: 72, flexShrink: 0 }} />
          ) : (
            <div className="art-thumb art-thumb--round" style={{ width: 72, height: 72, display: "grid", placeItems: "center", flexShrink: 0 }} aria-hidden>♪</div>
          )}
          <div style={{ flex: 1, minWidth: 0 }}>
            <h2 style={{ fontSize: 20, fontWeight: 600, letterSpacing: "-0.01em", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{name}</h2>
            {artist && artist.nb_fan > 0 && (
              <div style={{ fontSize: 12, color: "var(--muted)" }}>{artist.nb_fan.toLocaleString()} fans on Deezer</div>
            )}
          </div>
          <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">✕</button>
        </div>

        {/* Description */}
        {bioQ.isLoading ? (
          <p style={{ fontSize: 12, color: "var(--muted)", marginBottom: 16 }}>Looking up a description…</p>
        ) : bioQ.data?.description ? (
          <p style={{ fontSize: 12.5, lineHeight: 1.6, color: "var(--text)", marginBottom: 16 }}>{bioQ.data.description}</p>
        ) : null}

        {q.isLoading ? (
          <div className="card" style={{ color: "var(--muted)" }}>Loading…</div>
        ) : q.isError || !artist ? (
          <div className="card" style={{ color: "var(--muted)" }}>Couldn't load this artist from Deezer.</div>
        ) : (
          <>
            {topTracks.length > 0 && (
              <section style={{ marginBottom: 18 }}>
                <div className="section-label"><span>Top tracks</span></div>
                <div className="tracks-table">
                  {topTracks.map((t) => <TrackRow key={t.dz_track_id} t={t} />)}
                </div>
              </section>
            )}
            {albums.length > 0 && (
              <section style={{ marginBottom: 18 }}>
                <div className="section-label"><span>Albums</span></div>
                <div className="card" style={{ padding: 0 }}>
                  {albums.map((al) => <AlbumRow key={al.dz_album_id} al={al} />)}
                </div>
              </section>
            )}
            {singles.length > 0 && (
              <section>
                <div className="section-label"><span>Singles &amp; EPs</span></div>
                <div className="card" style={{ padding: 0 }}>
                  {singles.map((al) => <AlbumRow key={al.dz_album_id} al={al} />)}
                </div>
              </section>
            )}
            {topTracks.length === 0 && albums.length === 0 && singles.length === 0 && (
              <div className="card" style={{ color: "var(--muted)" }}>No catalog found for this artist on Deezer.</div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
