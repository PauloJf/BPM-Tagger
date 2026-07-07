import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import type { Playlist } from "../lib/types";
import { useTitle } from "../hooks/useTitle";
import { useGrabberStatus } from "../hooks/useGrabberStatus";
import { Toggle } from "../components/Toggle";

function Chips({ p }: { p: Playlist }) {
  return (
    <div className="pl-chips">
      <span className="chip chip--have">✓ {p.have_count}</span>
      <span className="chip chip--queued">↓ {p.queued_count}</span>
      <span className="chip chip--missing">✗ {p.missing_count}</span>
      <span className="chip chip--neutral">{p.track_count} total</span>
    </div>
  );
}

export default function Playlists() {
  useTitle("Playlists");
  const qc = useQueryClient();
  const status = useGrabberStatus();
  const [url, setUrl] = useState("");
  const [addErr, setAddErr] = useState("");

  const playlistsQ = useQuery({
    queryKey: ["playlists"],
    queryFn: () => api.get<{ playlists: Playlist[] }>("/api/playlists"),
    enabled: status.data?.enabled === true,
    refetchInterval: 10_000,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["playlists"] });
    qc.invalidateQueries({ queryKey: ["grabber-status"] });
  };

  const add = useMutation({
    mutationFn: (u: string) => api.post("/api/playlists", { url: u }),
    onSuccess: () => { setUrl(""); setAddErr(""); invalidate(); },
    onError: (e) => setAddErr(e instanceof ApiError ? e.message : "Failed to add playlist"),
  });
  const toggle = useMutation({
    mutationFn: (v: { id: number; enabled: boolean }) => api.patch(`/api/playlists/${v.id}`, { enabled: v.enabled }),
    onSuccess: invalidate,
  });
  const sync = useMutation({
    mutationFn: (id: number) => api.post(`/api/playlists/${id}/sync`),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/api/playlists/${id}`),
    onSuccess: invalidate,
  });

  if (status.data && !status.data.enabled) {
    return (
      <>
        <h1 style={{ fontSize: 28, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 16 }}>Playlists</h1>
        <div className="card" style={{ color: "var(--muted)", fontSize: 14 }}>
          The grabber is disabled. Enable it (and set Spotify credentials) in{" "}
          <Link to="/settings" style={{ color: "var(--accent-2)" }}>Settings → Grabber</Link>, then restart.
        </div>
      </>
    );
  }

  const connected = status.data?.spotify?.connected;
  const playlists = playlistsQ.data?.playlists ?? [];

  return (
    <>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 28, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 4 }}>Playlists</h1>
        <p style={{ fontSize: 13, color: "var(--muted)" }}>Watched Spotify playlists compared against your library.</p>
      </div>

      {!connected && (
        <div className="flash" style={{ background: "var(--warn-bg)", borderColor: "var(--warn-bd)", color: "var(--warn-fg)" }}>
          Spotify isn't connected.{" "}
          <Link to="/settings" style={{ color: "inherit", textDecoration: "underline" }}>Connect it in Settings</Link>{" "}
          to add and sync playlists.
        </div>
      )}

      {connected && (
        <div className="card" style={{ marginBottom: 18 }}>
          <div className="section-label"><span>Add a playlist</span></div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <input
              type="text"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="Spotify playlist URL or ID"
              style={{ flex: 1, minWidth: 220, fontFamily: "var(--mono)", fontSize: 12 }}
              onKeyDown={(e) => { if (e.key === "Enter" && url.trim()) add.mutate(url.trim()); }}
            />
            <button className="btn btn-primary btn-md" disabled={!url.trim() || add.isPending} onClick={() => add.mutate(url.trim())}>
              {add.isPending ? "Adding…" : "Add"}
            </button>
          </div>
          {addErr && <div style={{ color: "var(--err-fg)", fontSize: 12, marginTop: 8 }}>{addErr}</div>}
        </div>
      )}

      <div className="pl-list">
        {playlists.length === 0 ? (
          <div className="tracks-row-empty" style={{ borderRadius: 14, border: "1px solid var(--border)" }}>
            {playlistsQ.isLoading ? "Loading…" : "No playlists yet."}
          </div>
        ) : (
          playlists.map((p) => (
            <div key={p.id} className="pl-card">
              <Link to={`/playlist?id=${p.id}`} style={{ display: "flex", alignItems: "center", gap: 14, flex: 1, minWidth: 0, textDecoration: "none", color: "inherit" }}>
                {p.image_url ? (
                  <img src={p.image_url} alt="" className="pl-cover" referrerPolicy="no-referrer" />
                ) : (
                  <div className="pl-cover">♪</div>
                )}
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 6, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {p.name}
                  </div>
                  <Chips p={p} />
                </div>
              </Link>
              <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
                <Toggle on={!!p.enabled} onChange={(v) => toggle.mutate({ id: p.id, enabled: v })} label={`Auto-sync "${p.name}"`} />
                <button className="btn btn-ghost btn-sm" disabled={sync.isPending || !connected} onClick={() => sync.mutate(p.id)}>
                  {sync.isPending && sync.variables === p.id ? "Syncing…" : "Sync"}
                </button>
                <button className="btn btn-danger btn-sm" onClick={() => { if (confirm(`Remove "${p.name}"?`)) remove.mutate(p.id); }}>
                  Remove
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </>
  );
}
