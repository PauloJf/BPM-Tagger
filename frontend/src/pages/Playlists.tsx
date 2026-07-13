import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import type { Playlist, SpotifyPlaylist } from "../lib/types";
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
  const [browsing, setBrowsing] = useState(false);

  const playlistsQ = useQuery({
    queryKey: ["playlists"],
    queryFn: () => api.get<{ playlists: Playlist[] }>("/api/playlists"),
    enabled: status.data?.enabled === true,
    refetchInterval: 10_000,
  });

  const spotifyQ = useQuery({
    queryKey: ["spotify-playlists"],
    queryFn: () => api.get<{ playlists: SpotifyPlaylist[] }>("/api/spotify/playlists"),
    enabled: browsing && status.data?.spotify?.connected === true,
    staleTime: 60_000,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["playlists"] });
    qc.invalidateQueries({ queryKey: ["grabber-status"] });
    qc.invalidateQueries({ queryKey: ["spotify-playlists"] });
  };

  const add = useMutation({
    mutationFn: (body: { url: string } | { id: string }) => api.post("/api/playlists", body),
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
              onKeyDown={(e) => { if (e.key === "Enter" && url.trim()) add.mutate({ url: url.trim() }); }}
            />
            <button className="btn btn-primary btn-md" disabled={!url.trim() || add.isPending} onClick={() => add.mutate({ url: url.trim() })}>
              {add.isPending ? "Adding…" : "Add"}
            </button>
            <button className="btn btn-ghost btn-md" onClick={() => setBrowsing((b) => !b)}>
              {browsing ? "Hide my playlists" : "Browse my playlists"}
            </button>
          </div>
          {addErr && <div style={{ color: "var(--err-fg)", fontSize: 12, marginTop: 8 }}>{addErr}</div>}

          {browsing && (
            <div style={{ marginTop: 14, borderTop: "1px solid var(--border)", paddingTop: 12 }}>
              {spotifyQ.isLoading ? (
                <div style={{ fontSize: 13, color: "var(--muted)" }}>Loading your Spotify playlists…</div>
              ) : spotifyQ.isError ? (
                <div style={{ color: "var(--err-fg)", fontSize: 12 }}>
                  {spotifyQ.error instanceof ApiError ? spotifyQ.error.message : "Failed to load playlists"}
                </div>
              ) : (spotifyQ.data?.playlists ?? []).length === 0 ? (
                <div style={{ fontSize: 13, color: "var(--muted)" }}>No playlists on this Spotify account.</div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 320, overflowY: "auto" }}>
                  {(spotifyQ.data?.playlists ?? []).map((sp) => (
                    <div key={sp.spotify_id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 4px" }}>
                      {sp.image_url ? (
                        <img src={sp.image_url} alt="" className="pl-cover" style={{ width: 36, height: 36 }} referrerPolicy="no-referrer" />
                      ) : (
                        <div className="pl-cover" style={{ width: 36, height: 36 }}>♪</div>
                      )}
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <div style={{ fontSize: 13, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {sp.name}
                        </div>
                        <div style={{ fontSize: 11, color: "var(--muted)" }}>
                          {sp.owner ? `${sp.owner} · ` : ""}{sp.track_count} tracks
                        </div>
                      </div>
                      {sp.watched ? (
                        <span className="chip chip--have">✓ Watching</span>
                      ) : (
                        <button
                          className="btn btn-ghost btn-sm"
                          disabled={add.isPending}
                          onClick={() => add.mutate({ id: sp.spotify_id })}
                        >
                          {add.isPending && add.variables && "id" in add.variables && add.variables.id === sp.spotify_id ? "Adding…" : "Add"}
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
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
              <Link to={`/playlist?id=${p.id}`} className="pl-card-main">
                {p.image_url ? (
                  <img src={p.image_url} alt="" className="pl-cover" referrerPolicy="no-referrer" />
                ) : (
                  <div className="pl-cover">♪</div>
                )}
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 6, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {p.name}
                  </div>
                  <Chips p={p} />
                </div>
              </Link>
              <div className="pl-card-actions">
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
