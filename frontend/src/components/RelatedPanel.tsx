import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { RelatedArtist, RelatedTrack } from "../lib/types";
import { useGrabberStatus } from "../hooks/useGrabberStatus";
import { useSuggestionQueue } from "../hooks/useSuggestionQueue";
import { PreviewButton } from "./trackBits";
import ArtistModal from "./ArtistModal";

/** A single similar/related track row (Search-page style). */
function TrackRow({ t, grabberEnabled }: { t: RelatedTrack; grabberEnabled: boolean }) {
  const add = useSuggestionQueue();
  const adding = add.isPending && add.variables?.dz_track_id === t.dz_track_id;
  return (
    <div className="pl-track-row" style={{ gridTemplateColumns: "1fr auto", gap: 8 }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.title}</div>
        <div style={{ fontSize: 11, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {t.artist}{t.album ? ` · ${t.album}` : ""}
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        {t.preview_url && <PreviewButton track={{ dz_track_id: t.dz_track_id, title: t.title, artist: t.artist, preview_url: t.preview_url }} />}
        {t.in_library ? (
          t.file_path ? (
            <Link className="chip chip--have" to={`/track?path=${encodeURIComponent(t.file_path)}`}>✓ in library</Link>
          ) : (
            <span className="chip chip--have">✓ in library</span>
          )
        ) : t.queued ? (
          <span className="chip chip--queued">↓ queued</span>
        ) : grabberEnabled ? (
          <button
            className="btn btn-soft btn-sm"
            disabled={adding}
            onClick={() => add.mutate({
              dz_track_id: t.dz_track_id, title: t.title, artist: t.artist,
              album: t.album, duration_ms: t.duration_ms, cover_url: t.cover_url,
            })}
          >Add to queue</button>
        ) : null}
      </div>
    </div>
  );
}

/** One related-artist row: badge by library track_count, opens the artist modal. */
function ArtistRow({ a, onOpen }: { a: RelatedArtist; onOpen: () => void }) {
  const badge = a.track_count >= 3 ? (
    <Link className="chip chip--have" to={`/artist?name=${encodeURIComponent(a.library_name || a.name)}`} onClick={(e) => e.stopPropagation()}>✓ {a.track_count} tracks</Link>
  ) : a.track_count > 0 ? (
    <Link className="chip chip--neutral" to={`/artist?name=${encodeURIComponent(a.library_name || a.name)}`} onClick={(e) => e.stopPropagation()}>
      {a.track_count} track{a.track_count === 1 ? "" : "s"}
    </Link>
  ) : null;

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 10px", borderBottom: "1px solid var(--border)" }}>
      <button
        onClick={onOpen}
        title={`Explore ${a.name}`}
        style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center", gap: 10, background: "none", border: "none", padding: 0, cursor: "pointer", color: "inherit", textAlign: "left" }}
      >
        {a.image_url ? (
          <img src={a.image_url} alt="" loading="lazy" className="art-thumb art-thumb--round" style={{ width: 36, height: 36, flexShrink: 0 }} />
        ) : (
          <div className="art-thumb art-thumb--round" style={{ width: 36, height: 36, display: "grid", placeItems: "center", flexShrink: 0 }} aria-hidden>♪</div>
        )}
        <span style={{ flex: 1, minWidth: 0, fontSize: 13, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={a.name}>{a.name}</span>
      </button>
      {badge}
      <button className="btn btn-bare btn-sm" style={{ padding: "2px 8px" }} onClick={onOpen} title="Explore artist" aria-label="Explore artist">›</button>
    </div>
  );
}

/** Shared "Related · powered by Deezer" panel for the Artist / Album / Track
 *  pages: collapsed by default, live similar-artists + similar-tracks lookups
 *  fetched only on first expand. Read-only (works with the grabber off); the
 *  Add-to-queue action only appears when the grabber is enabled. */
export default function RelatedPanel({ artist }: { artist: string; context?: "artist" | "album" | "track" }) {
  const [open, setOpen] = useState(false);
  const [modalArtist, setModalArtist] = useState<{ dzId: string; name: string } | null>(null);
  const grabber = useGrabberStatus();
  const grabberEnabled = grabber.data?.enabled === true;
  const name = (artist || "").trim();

  const artistsQ = useQuery({
    queryKey: ["related-artists", name],
    queryFn: () => api.get<{ artists: RelatedArtist[] }>(`/api/related/artists?name=${encodeURIComponent(name)}`),
    enabled: open && !!name,
    staleTime: Infinity,
  });
  const tracksQ = useQuery({
    queryKey: ["related-tracks", name],
    queryFn: () => api.get<{ tracks: RelatedTrack[] }>(`/api/related/tracks?name=${encodeURIComponent(name)}`),
    enabled: open && !!name,
    staleTime: Infinity,
  });

  if (!name) return null;

  const artists = artistsQ.data?.artists ?? [];
  const tracks = tracksQ.data?.tracks ?? [];
  const loading = artistsQ.isLoading || tracksQ.isLoading;
  const empty = !loading && artists.length === 0 && tracks.length === 0;

  return (
    <div style={{ marginTop: 24 }}>
      <button
        className="section-label"
        style={{ width: "100%", background: "none", border: "none", cursor: "pointer", color: "inherit", padding: 0, alignItems: "center" }}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span>Related · powered by Deezer</span>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"
          style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform 0.15s" }}>
          <path d="M9 6l6 6-6 6" />
        </svg>
      </button>

      {open && (
        <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 18 }}>
          {loading ? (
            <div className="card" style={{ color: "var(--muted)" }}>Loading…</div>
          ) : empty ? (
            <div className="card" style={{ color: "var(--muted)" }}>Nothing found on Deezer for this artist.</div>
          ) : (
            <>
              {artists.length > 0 && (
                <div>
                  <div style={{ fontSize: 11, fontFamily: "var(--mono)", textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--muted)", marginBottom: 6 }}>Similar artists</div>
                  <div className="card" style={{ padding: 0 }}>
                    {artists.map((a) => (
                      <ArtistRow
                        key={a.dz_id}
                        a={a}
                        onOpen={() => setModalArtist({ dzId: a.dz_id, name: a.name })}
                      />
                    ))}
                  </div>
                </div>
              )}
              {tracks.length > 0 && (
                <div>
                  <div style={{ fontSize: 11, fontFamily: "var(--mono)", textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--muted)", marginBottom: 6 }}>Similar tracks</div>
                  <div className="tracks-table">
                    {tracks.map((t) => (
                      <TrackRow key={t.dz_track_id} t={t} grabberEnabled={grabberEnabled} />
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {modalArtist && (
        <ArtistModal dzId={modalArtist.dzId} name={modalArtist.name} onClose={() => setModalArtist(null)} />
      )}
    </div>
  );
}
