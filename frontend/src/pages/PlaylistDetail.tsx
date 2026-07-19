import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Playlist, PlaylistTrack } from "../lib/types";
import { useTitle } from "../hooks/useTitle";
import { useGrabberStatus } from "../hooks/useGrabberStatus";

function StatusChip({ s }: { s: string }) {
  if (s === "have") return <span className="chip chip--have">✓ have</span>;
  if (s === "queued") return <span className="chip chip--queued">↓ queued</span>;
  if (s === "removed") return <span className="chip chip--removed">− removed</span>;
  return <span className="chip chip--missing">✗ missing</span>;
}

/** Milliseconds → "m:ss" (blank when unknown). */
function fmtDur(ms: number | null | undefined): string {
  if (!ms || ms <= 0) return "";
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function syncedLabel(iso: string | null): string {
  if (!iso) return "never synced";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "never synced";
  return `synced ${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}

export default function PlaylistDetail() {
  const [params] = useSearchParams();
  const id = params.get("id");
  const qc = useQueryClient();
  const status = useGrabberStatus();
  const [tab, setTab] = useState("");

  const tracksQ = useQuery({
    queryKey: ["playlist-tracks", id, tab],
    queryFn: () => api.get<{ playlist: Playlist; tracks: PlaylistTrack[] }>(
      `/api/playlists/${id}/tracks${tab ? `?status=${tab}` : ""}`),
    enabled: !!id,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["playlist-tracks", id] });
    qc.invalidateQueries({ queryKey: ["playlists"] });
    qc.invalidateQueries({ queryKey: ["grabber-status"] });
  };
  const sync = useMutation({ mutationFn: () => api.post(`/api/playlists/${id}/sync`), onSuccess: invalidate });
  const enqueue = useMutation({
    mutationFn: () => api.post<{ enqueued: number }>(`/api/playlists/${id}/enqueue-missing`),
    onSuccess: invalidate,
  });
  const removeTrack = useMutation({
    mutationFn: (ptId: number) => api.del(`/api/playlists/${id}/tracks/${ptId}`),
    onSuccess: invalidate,
  });

  const pl = tracksQ.data?.playlist;
  useTitle(pl?.name || "Playlist");
  const tracks = tracksQ.data?.tracks ?? [];
  const spotifyConnected = status.data?.spotify?.connected === true;
  const grabberEnabled = status.data?.enabled === true;
  const isSpotify = pl?.source === "spotify";
  const isLocal = pl?.source === "local";
  const canSync = pl?.source === "navidrome" || (isSpotify && spotifyConnected);
  // Queuing missing tracks works for any synced source now (Phase 5) — a Navidrome
  // playlist's missing tracks are grabbed by metadata via the shared enqueue helper.
  // Gated on the grabber being enabled; Spotify additionally needs a live connection.
  const canQueueMissing = !isLocal && grabberEnabled && (pl?.missing_count ?? 0) > 0;

  const tabs = [
    { key: "", label: "All" },
    { key: "have", label: "Have" },
    { key: "missing", label: "Missing" },
    ...(!isLocal ? [{ key: "queued", label: "Queued" }] : []),
    ...((pl?.removed_count ?? 0) > 0 ? [{ key: "removed", label: "Removed" }] : []),
  ];

  if (!id) return <p style={{ color: "var(--muted)" }}>No playlist selected.</p>;

  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 18, flexWrap: "wrap" }}>
        <Link to="/playlists" className="btn btn-bare btn-sm" style={{ paddingLeft: 0 }}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
            <path d="M19 12 H5 M11 6 L5 12 L11 18" />
          </svg>
          Playlists
        </Link>
        <div style={{ flex: 1 }} />
        {canQueueMissing && (
          <button
            className="btn btn-soft btn-sm"
            disabled={enqueue.isPending || (isSpotify && !spotifyConnected)}
            title={isSpotify && !spotifyConnected ? "Connect Spotify to queue missing tracks" : "Grab the missing tracks via the download providers"}
            onClick={() => enqueue.mutate()}
          >
            {enqueue.isPending ? "Enqueuing…" : `Enqueue missing (${pl?.missing_count})`}
          </button>
        )}
        {(pl?.have_count ?? 0) > 0 && (
          <a className="btn btn-ghost btn-sm" href={`/api/playlists/${id}/export.m3u`}>Export .m3u</a>
        )}
        {!isLocal && (
          <button className="btn btn-ghost btn-sm" disabled={sync.isPending || !canSync} onClick={() => sync.mutate()}>
            {sync.isPending ? "Syncing…" : "Sync now"}
          </button>
        )}
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 20, flexWrap: "wrap" }}>
        {pl?.image_url ? (
          <img src={pl.image_url} alt="" className="pl-cover" style={{ width: 72, height: 72 }} referrerPolicy="no-referrer" />
        ) : null}
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 8 }}>{pl?.name || "…"}</h1>
          {pl && (
            <div className="pl-chips">
              <span className="chip chip--have">✓ {pl.have_count} have</span>
              {pl.queued_count > 0 && <span className="chip chip--queued">↓ {pl.queued_count} queued</span>}
              <span className="chip chip--missing">✗ {pl.missing_count} missing</span>
              {pl.removed_count > 0 && <span className="chip chip--removed">− {pl.removed_count} removed</span>}
              <span className="chip chip--neutral">{pl.track_count} total</span>
              <span className="chip chip--neutral" style={{ textTransform: "none" }}>
                {isLocal ? "local playlist" : syncedLabel(pl.last_synced_at)}
              </span>
            </div>
          )}
        </div>
      </div>

      <div className="filter-pills" style={{ marginBottom: 16, width: "fit-content" }}>
        {tabs.map((t) => (
          <button key={t.key} className={"filter-pill" + (tab === t.key ? " active" : "")} onClick={() => setTab(t.key)}>
            {t.label}
          </button>
        ))}
      </div>

      <div className="tracks-table">
        {tracks.length === 0 ? (
          <div className="tracks-row-empty">
            {tracksQ.isLoading ? "Loading…"
              : isLocal ? "No tracks yet — add tracks from a track page or the library “add to playlist” button."
              : "No tracks."}
          </div>
        ) : (
          tracks.map((t) => {
            // 'have' rows are matched to a local file → link into the library and
            // show its real BPM. Missing/queued/removed rows stay plain text.
            const inLib = t.derived_status === "have" && !!t.matched_file_path;
            const label = t.title || "this track";
            const artist = t.local_artist || t.artist;
            const album = t.local_album || t.album;
            const albumArtist = t.local_album_artist || album || artist;
            const dur = fmtDur(t.local_duration_ms ?? t.duration_ms);
            return (
              <div key={t.id} className={"pl-track-row" + (t.removed_at ? " pl-track-row--removed" : "")}>
                <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--muted)" }}>
                  {String(t.position + 1).padStart(2, "0")}
                </span>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", display: "flex", alignItems: "center", gap: 6 }}>
                    {inLib ? (
                      <Link to={`/track?path=${encodeURIComponent(t.matched_file_path!)}`} style={{ color: "inherit", textDecoration: "none", overflow: "hidden", textOverflow: "ellipsis" }} title="Open the track page">
                        {t.title}
                      </Link>
                    ) : (
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{t.title}</span>
                    )}
                    {!!t.is_new && !t.removed_at && <span className="chip chip--new" title="Added since you last viewed">✦ new</span>}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {inLib ? (
                      <>
                        <Link to={`/artist?name=${encodeURIComponent(artist)}`} style={{ color: "inherit", textDecoration: "none" }}>{artist}</Link>
                        {album && <> · <Link to={`/album?album=${encodeURIComponent(album)}&album_artist=${encodeURIComponent(albumArtist)}`} style={{ color: "inherit", textDecoration: "none" }}>{album}</Link></>}
                      </>
                    ) : (
                      <>{t.artist}{t.album ? ` · ${t.album}` : ""}</>
                    )}
                  </div>
                </div>
                <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 3 }}>
                  <StatusChip s={t.derived_status} />
                  {(inLib && t.local_bpm != null) || dur ? (
                    <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)", whiteSpace: "nowrap" }}>
                      {inLib && t.local_bpm != null ? `${Math.round(t.local_bpm)} BPM` : ""}
                      {inLib && t.local_bpm != null && dur ? " · " : ""}
                      {dur}
                    </span>
                  ) : null}
                  {isLocal && (
                    <button
                      className="btn btn-bare btn-sm"
                      style={{ padding: "2px 4px", color: "var(--muted)" }}
                      disabled={removeTrack.isPending}
                      aria-label={`Remove ${label}`}
                      title="Remove from playlist"
                      onClick={() => { if (confirm(`Remove “${label}” from this playlist?`)) removeTrack.mutate(t.id); }}
                    >
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M18 6 L6 18 M6 6 l12 12" />
                      </svg>
                    </button>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>
    </>
  );
}
