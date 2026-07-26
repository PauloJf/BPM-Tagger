import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import type { NavidromePlaylist, Playlist, PlaylistSource, SpotifyPlaylist } from "../lib/types";
import { useTitle } from "../hooks/useTitle";
import { useGrabberStatus } from "../hooks/useGrabberStatus";
import { Toggle } from "../components/Toggle";
import PageHeader from "../components/PageHeader";
import { PlaylistCover } from "../components/Artwork";

const SOURCE_META: Record<PlaylistSource, { label: string; glyph: string; cls: string }> = {
  spotify: { label: "Spotify", glyph: "●", cls: "pl-src--spotify" },
  navidrome: { label: "Navidrome", glyph: "☁", cls: "pl-src--navidrome" },
  local: { label: "Local", glyph: "♪", cls: "pl-src--local" },
};

function SourceBadge({ source }: { source: PlaylistSource }) {
  const m = SOURCE_META[source] ?? SOURCE_META.local;
  return <span className={`pl-src ${m.cls}`} title={`${m.label} playlist`}>{m.glyph} {m.label}</span>;
}

function Chips({ p }: { p: Playlist }) {
  return (
    <div className="pl-chips">
      <span className="chip chip--have">✓ {p.have_count}</span>
      {p.queued_count > 0 && <span className="chip chip--queued">↓ {p.queued_count}</span>}
      <span className="chip chip--missing">✗ {p.missing_count}</span>
      {p.new_count > 0 && <span className="chip chip--new">✦ {p.new_count} new</span>}
      {p.removed_count > 0 && <span className="chip chip--removed">− {p.removed_count} removed</span>}
      <span className="chip chip--neutral">{p.track_count} total</span>
    </div>
  );
}

type AddBody =
  | { url: string }
  | { id: string }
  | { source: "navidrome"; navidrome_id: string; name: string }
  | { source: "local"; name: string };

export default function Playlists() {
  useTitle("Playlists");
  const qc = useQueryClient();
  const status = useGrabberStatus();
  const [url, setUrl] = useState("");
  const [localName, setLocalName] = useState("");
  const [addErr, setAddErr] = useState("");
  const [browsing, setBrowsing] = useState<null | "spotify" | "navidrome">(null);

  const playlistsQ = useQuery({
    queryKey: ["playlists"],
    queryFn: () => api.get<{ playlists: Playlist[] }>("/api/playlists"),
    refetchInterval: 10_000,
  });

  const spotifyConnected = status.data?.spotify?.connected === true;

  const spotifyQ = useQuery({
    queryKey: ["spotify-playlists"],
    queryFn: () => api.get<{ playlists: SpotifyPlaylist[] }>("/api/spotify/playlists"),
    enabled: browsing === "spotify" && spotifyConnected,
    staleTime: 60_000,
  });

  const navidromeQ = useQuery({
    queryKey: ["navidrome-playlists"],
    queryFn: () => api.get<{ playlists: NavidromePlaylist[] }>("/api/navidrome/playlists"),
    enabled: browsing === "navidrome",
    retry: false,
    staleTime: 60_000,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["playlists"] });
    qc.invalidateQueries({ queryKey: ["grabber-status"] });
    qc.invalidateQueries({ queryKey: ["spotify-playlists"] });
    qc.invalidateQueries({ queryKey: ["navidrome-playlists"] });
  };

  const add = useMutation({
    mutationFn: (body: AddBody) => api.post("/api/playlists", body),
    onSuccess: () => { setUrl(""); setLocalName(""); setAddErr(""); invalidate(); },
    onError: (e) => setAddErr(e instanceof ApiError ? e.message : "Failed to add playlist"),
  });
  const addLocal = () => {
    const name = localName.trim();
    if (name) add.mutate({ source: "local", name });
  };
  const toggle = useMutation({
    mutationFn: (v: { id: number; enabled: boolean }) => api.patch(`/api/playlists/${v.id}`, { enabled: v.enabled }),
    onSuccess: invalidate,
  });
  // Pinning is the playlist list's "custom ordering": the server sorts pinned
  // first, then alphabetically, so this only has to refetch.
  const pin = useMutation({
    mutationFn: (v: { id: number; pinned: boolean }) => api.patch(`/api/playlists/${v.id}`, { pinned: v.pinned }),
    onSuccess: () => { invalidate(); qc.invalidateQueries({ queryKey: ["run-playlists"] }); },
  });
  const sync = useMutation({
    mutationFn: (id: number) => api.post(`/api/playlists/${id}/sync`),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/api/playlists/${id}`),
    onSuccess: invalidate,
  });

  const playlists = playlistsQ.data?.playlists ?? [];
  const navidromeUnconfigured =
    navidromeQ.isError && navidromeQ.error instanceof ApiError &&
    navidromeQ.error.message === "navidrome_not_configured";

  const canSync = (p: Playlist) => p.source === "navidrome" || (p.source === "spotify" && spotifyConnected);

  return (
    <>
      <PageHeader
        title="Playlists"
        subtitle={
          playlists.length > 0
            ? <><span style={{ fontFamily: "var(--mono)", color: "var(--text)" }}>{playlists.length}</span> playlist{playlists.length === 1 ? "" : "s"} compared against your library</>
            : "Spotify and Navidrome playlists compared against your library."
        }
      />

      <div className="card" style={{ marginBottom: 18 }}>
        <div className="section-label"><span>Add a playlist</span></div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          {spotifyConnected && (
            <>
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
              <button className="btn btn-ghost btn-md" onClick={() => setBrowsing((b) => b === "spotify" ? null : "spotify")}>
                {browsing === "spotify" ? "Hide Spotify" : "Browse Spotify"}
              </button>
            </>
          )}
          <button className="btn btn-ghost btn-md" onClick={() => setBrowsing((b) => b === "navidrome" ? null : "navidrome")}>
            {browsing === "navidrome" ? "Hide Navidrome" : "Browse Navidrome"}
          </button>
        </div>

        {/* Local playlist: a name is all it takes — tracks are added later from
            the library (track pages / rows). No source, no sync, no grabber. */}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginTop: 8 }}>
          <input
            type="text"
            value={localName}
            onChange={(e) => setLocalName(e.target.value)}
            placeholder="New local playlist name"
            maxLength={120}
            style={{ flex: 1, minWidth: 220, fontSize: 13 }}
            onKeyDown={(e) => { if (e.key === "Enter") addLocal(); }}
          />
          <button className="btn btn-ghost btn-md" disabled={!localName.trim() || add.isPending} onClick={addLocal}>
            Create local playlist
          </button>
        </div>
        {!spotifyConnected && (
          <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 8 }}>
            Spotify isn't connected — <Link to="/settings" style={{ color: "var(--accent-2)" }}>connect it in Settings</Link> to add Spotify playlists.
          </div>
        )}
        {addErr && <div style={{ color: "var(--err-fg)", fontSize: 12, marginTop: 8 }}>{addErr}</div>}

        {browsing === "spotify" && (
          <BrowseList
            loading={spotifyQ.isLoading}
            error={spotifyQ.isError ? (spotifyQ.error instanceof ApiError ? spotifyQ.error.message : "Failed to load playlists") : null}
            empty={(spotifyQ.data?.playlists ?? []).length === 0 ? "No playlists on this Spotify account." : null}
          >
            {(spotifyQ.data?.playlists ?? []).map((sp) => (
              <BrowseRow
                key={sp.spotify_id}
                image={sp.image_url}
                name={sp.name}
                sub={`${sp.owner ? `${sp.owner} · ` : ""}${sp.track_count} tracks`}
                watched={sp.watched}
                adding={add.isPending && !!add.variables && "id" in add.variables && add.variables.id === sp.spotify_id}
                onAdd={() => add.mutate({ id: sp.spotify_id })}
              />
            ))}
          </BrowseList>
        )}

        {browsing === "navidrome" && (
          <BrowseList
            loading={navidromeQ.isLoading}
            error={navidromeUnconfigured ? null : navidromeQ.isError ? "Failed to load Navidrome playlists" : null}
            empty={
              navidromeUnconfigured ? null :
              (navidromeQ.data?.playlists ?? []).length === 0 ? "No Navidrome playlists found." : null
            }
          >
            {navidromeUnconfigured ? (
              <div style={{ fontSize: 13, color: "var(--muted)" }}>
                Navidrome isn't configured — set its URL and credentials in{" "}
                <Link to="/settings" style={{ color: "var(--accent-2)" }}>Settings</Link>.
              </div>
            ) : (
              (navidromeQ.data?.playlists ?? []).map((n) => (
                <BrowseRow
                  key={n.navidrome_id}
                  image={n.image_url}
                  name={n.name}
                  sub={`${n.track_count} tracks`}
                  watched={n.watched}
                  adding={add.isPending && !!add.variables && "navidrome_id" in add.variables && add.variables.navidrome_id === n.navidrome_id}
                  onAdd={() => add.mutate({ source: "navidrome", navidrome_id: n.navidrome_id, name: n.name })}
                />
              ))
            )}
          </BrowseList>
        )}
      </div>

      <div className="pl-list">
        {playlists.length === 0 ? (
          <div className="tracks-row-empty" style={{ borderRadius: 14, border: "1px solid var(--border)" }}>
            {playlistsQ.isLoading ? "Loading…" : "No playlists yet."}
          </div>
        ) : (
          playlists.map((p) => (
            <div key={p.id} className="pl-card">
              <Link to={`/playlist?id=${p.id}`} className="pl-card-main">
                {/* Local playlists have no image_url; their art is served (custom
                    pick, else an auto-collage of their tracks) by the cover
                    endpoint, which 404s into the same ♪ placeholder. */}
                {p.source === "local" ? (
                  <PlaylistCover id={p.id} size={44} />
                ) : p.image_url ? (
                  <img src={p.image_url} alt="" className="pl-cover" referrerPolicy="no-referrer" />
                ) : (
                  <div className="pl-cover">♪</div>
                )}
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                    {!!p.pinned && <span className="pl-pinned" title="Pinned to the top" aria-hidden="true">📌</span>}
                    <span style={{ fontSize: 15, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {p.name}
                    </span>
                    <SourceBadge source={p.source} />
                  </div>
                  {p.description && (
                    <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 6, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {p.description}
                    </div>
                  )}
                  <Chips p={p} />
                </div>
              </Link>
              <div className="pl-card-actions">
                <button
                  className="btn btn-bare btn-sm"
                  style={{ padding: "2px 6px", opacity: p.pinned ? 1 : 0.45 }}
                  aria-pressed={!!p.pinned}
                  aria-label={p.pinned ? `Unpin "${p.name}"` : `Pin "${p.name}"`}
                  title={p.pinned ? "Unpin — sort with the rest" : "Pin to the top of the list"}
                  disabled={pin.isPending}
                  onClick={() => pin.mutate({ id: p.id, pinned: !p.pinned })}
                >
                  📌
                </button>
                {p.source === "spotify" && (
                  <Toggle on={!!p.enabled} onChange={(v) => toggle.mutate({ id: p.id, enabled: v })} label={`Auto-sync "${p.name}"`} />
                )}
                {p.source !== "local" && (
                  <button className="btn btn-ghost btn-sm" disabled={sync.isPending || !canSync(p)} onClick={() => sync.mutate(p.id)}>
                    {sync.isPending && sync.variables === p.id ? "Syncing…" : "Sync"}
                  </button>
                )}
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

function BrowseList({ loading, error, empty, children }: {
  loading: boolean; error: string | null; empty: string | null; children: React.ReactNode;
}) {
  return (
    <div style={{ marginTop: 14, borderTop: "1px solid var(--border)", paddingTop: 12 }}>
      {loading ? (
        <div style={{ fontSize: 13, color: "var(--muted)" }}>Loading…</div>
      ) : error ? (
        <div style={{ color: "var(--err-fg)", fontSize: 12 }}>{error}</div>
      ) : empty ? (
        <div style={{ fontSize: 13, color: "var(--muted)" }}>{empty}</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 320, overflowY: "auto" }}>
          {children}
        </div>
      )}
    </div>
  );
}

function BrowseRow({ image, name, sub, watched, adding, onAdd }: {
  image: string | null; name: string; sub: string; watched: boolean; adding: boolean; onAdd: () => void;
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 4px" }}>
      {image ? (
        <img src={image} alt="" className="pl-cover" style={{ width: 36, height: 36 }} referrerPolicy="no-referrer" />
      ) : (
        <div className="pl-cover" style={{ width: 36, height: 36 }}>♪</div>
      )}
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ fontSize: 13, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{name}</div>
        <div style={{ fontSize: 11, color: "var(--muted)" }}>{sub}</div>
      </div>
      {watched ? (
        <span className="chip chip--have">✓ Watching</span>
      ) : (
        <button className="btn btn-ghost btn-sm" disabled={adding} onClick={onAdd}>
          {adding ? "Adding…" : "Add"}
        </button>
      )}
    </div>
  );
}
