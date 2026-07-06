import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Playlist, PlaylistTrack } from "../lib/types";
import { useTitle } from "../hooks/useTitle";
import { useGrabberStatus } from "../hooks/useGrabberStatus";

const TABS: { key: string; label: string }[] = [
  { key: "", label: "All" },
  { key: "have", label: "Have" },
  { key: "missing", label: "Missing" },
  { key: "queued", label: "Queued" },
];

function StatusChip({ s }: { s: string }) {
  if (s === "have") return <span className="chip chip--have">✓ have</span>;
  if (s === "queued") return <span className="chip chip--queued">↓ queued</span>;
  return <span className="chip chip--missing">✗ missing</span>;
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

  const pl = tracksQ.data?.playlist;
  useTitle(pl?.name || "Playlist");
  const tracks = tracksQ.data?.tracks ?? [];
  const connected = status.data?.spotify?.connected;

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
        {(pl?.missing_count ?? 0) > 0 && (
          <button className="btn btn-soft btn-sm" disabled={enqueue.isPending || !connected} onClick={() => enqueue.mutate()}>
            {enqueue.isPending ? "Enqueuing…" : `Enqueue missing (${pl?.missing_count})`}
          </button>
        )}
        <button className="btn btn-ghost btn-sm" disabled={sync.isPending || !connected} onClick={() => sync.mutate()}>
          {sync.isPending ? "Syncing…" : "Sync now"}
        </button>
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
              <span className="chip chip--queued">↓ {pl.queued_count} queued</span>
              <span className="chip chip--missing">✗ {pl.missing_count} missing</span>
              <span className="chip chip--neutral">{pl.track_count} total</span>
            </div>
          )}
        </div>
      </div>

      <div className="filter-pills" style={{ marginBottom: 16, width: "fit-content" }}>
        {TABS.map((t) => (
          <button key={t.key} className={"filter-pill" + (tab === t.key ? " active" : "")} onClick={() => setTab(t.key)}>
            {t.label}
          </button>
        ))}
      </div>

      <div className="tracks-table">
        {tracks.length === 0 ? (
          <div className="tracks-row-empty">{tracksQ.isLoading ? "Loading…" : "No tracks."}</div>
        ) : (
          tracks.map((t) => (
            <div key={t.id} className="pl-track-row">
              <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--muted)" }}>
                {String(t.position + 1).padStart(2, "0")}
              </span>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.title}</div>
                <div style={{ fontSize: 11, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {t.artist}{t.album ? ` · ${t.album}` : ""}
                </div>
              </div>
              <div style={{ textAlign: "right" }}>
                <StatusChip s={t.derived_status} />
              </div>
            </div>
          ))
        )}
      </div>
    </>
  );
}
