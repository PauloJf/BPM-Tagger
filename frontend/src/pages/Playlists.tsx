import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
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

/** Per-preset runnable counts for the library and each playlist. One fetch
 *  serves both the cadence strip and the per-card badges. */
interface Readiness {
  presets: Array<{ name: string; bpm: number }>;
  stretch_limit_pct: number;
  library: Record<string, number>;
  playlists: Array<{ id: number; name: string; counts: Record<string, number> }>;
}

const RUN_SOURCE_KEY = "bpm.run.source";

/** Quiet per-preset counts under a card's coverage chips: how much of this
 *  playlist you could actually run at each cadence. Clicking one preselects the
 *  playlist as the Run source (the Run page reads it from localStorage) and
 *  deep-links the target. Renders nothing when no preset has a match, so the
 *  card doesn't grow an empty row.
 *
 *  Buttons, not links: the whole card body is already an <a> to the playlist,
 *  and an <a> inside an <a> is invalid HTML that browsers un-nest — so these
 *  navigate programmatically instead. */
function ReadinessBadges({ counts, presets, stretchPct, playlistId, name, onPick }: {
  counts: Record<string, number>;
  presets: Array<{ name: string; bpm: number }>;
  stretchPct: number;
  playlistId: number;
  name: string;
  onPick: (playlistId: number, bpm: number) => void;
}) {
  const hits = presets.filter((p) => (counts[String(p.bpm)] ?? 0) > 0);
  if (hits.length === 0) return null;
  return (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 5 }}>
      {hits.map((p) => (
        <button
          key={p.bpm}
          type="button"
          className="pl-ready-badge"
          onClick={(e) => { e.preventDefault(); e.stopPropagation(); onPick(playlistId, p.bpm); }}
          title={`${counts[String(p.bpm)]} tracks in “${name}” runnable at ${p.name} (${p.bpm} BPM) within ±${stretchPct.toFixed(1)}% — run this playlist at that cadence`}
        >
          {p.bpm}:{counts[String(p.bpm)]}
        </button>
      ))}
    </div>
  );
}

type AddBody =
  | { url: string }
  | { id: string }
  | { source: "navidrome"; navidrome_id: string; name: string }
  | { source: "local"; name: string };

// ── Playlist operations (compare / merge) ───────────────────────────────────
//
// Both are local-first: compare only reads, and merge only ever writes a Local
// playlist. They share one collapsible card under the header rather than a menu
// per card, because both take *several* playlists as input — picking them from
// the grid one card at a time is the interaction this avoids.

interface MergeSourceReport {
  id: number;
  name: string;
  added: number;
  already_present: number;
  skipped_duplicate: number;
  not_in_library: number;
}

export interface MergeReport {
  target: Playlist;
  sources: MergeSourceReport[];
  totals: Omit<MergeSourceReport, "id" | "name">;
}

/** "Added 12 · 3 already there · 2 duplicates · 1 not in library" — the same
 *  reporting shape (and vocabulary) the Add-all-to-playlist toast uses, with the
 *  cross-source duplicate skip merge adds. Zero counts are dropped so a clean
 *  merge reads as one number, not four. Exported for the component test. */
export function mergeSummary(c: Omit<MergeSourceReport, "id" | "name">): string {
  const parts = [`Added ${c.added}`];
  if (c.already_present) parts.push(`${c.already_present} already there`);
  if (c.skipped_duplicate) parts.push(`${c.skipped_duplicate} duplicate${c.skipped_duplicate === 1 ? "" : "s"}`);
  if (c.not_in_library) parts.push(`${c.not_in_library} not in library`);
  return parts.join(" · ");
}

function ComparePanel({ playlists }: { playlists: Playlist[] }) {
  const navigate = useNavigate();
  const [a, setA] = useState<number | "">("");
  const [b, setB] = useState<number | "">("");
  const ready = a !== "" && b !== "" && a !== b;
  const options = (skip: number | "") =>
    playlists.filter((p) => p.id !== skip).map((p) => <option key={p.id} value={p.id}>{p.name}</option>);
  return (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
      <select aria-label="First playlist" value={a} style={{ fontSize: 13, minWidth: 180 }}
        onChange={(e) => setA(e.target.value ? Number(e.target.value) : "")}>
        <option value="">Choose a playlist…</option>
        {options(b)}
      </select>
      <span style={{ fontSize: 12, color: "var(--muted)" }}>vs</span>
      <select aria-label="Second playlist" value={b} style={{ fontSize: 13, minWidth: 180 }}
        onChange={(e) => setB(e.target.value ? Number(e.target.value) : "")}>
        <option value="">Choose a playlist…</option>
        {options(a)}
      </select>
      <button className="btn btn-primary btn-md" disabled={!ready}
        onClick={() => navigate(`/playlist-diff?a=${a}&b=${b}`)}>
        Compare
      </button>
      <span style={{ fontSize: 12, color: "var(--muted)" }}>
        Read-only — shows what they share and what only one of them has.
      </span>
    </div>
  );
}

function MergePanel({ playlists, onDone }: { playlists: Playlist[]; onDone: () => void }) {
  const [picked, setPicked] = useState<number[]>([]);
  const [targetId, setTargetId] = useState<number | "new">("new");
  const [newName, setNewName] = useState("");
  const [report, setReport] = useState<MergeReport | null>(null);
  const [err, setErr] = useState("");
  const locals = playlists.filter((p) => p.source === "local");

  const merge = useMutation({
    mutationFn: (body: { source_ids: number[]; target: { id: number } | { name: string } }) =>
      api.post<MergeReport>("/api/playlists/merge", body),
    onSuccess: (r) => { setReport(r); setErr(""); onDone(); },
    onError: (e) => { setReport(null); setErr(e instanceof ApiError ? e.message : "Merge failed"); },
  });

  const target = targetId === "new" ? { name: newName.trim() } : { id: targetId };
  const ready = picked.length >= 2 && (targetId !== "new" || !!newName.trim());

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ fontSize: 12, color: "var(--muted)" }}>
        Pick two or more playlists; their library tracks are copied into one local
        playlist, deduplicated by file, ISRC, then artist + title. The sources are
        never touched.
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, maxHeight: 200, overflowY: "auto" }}>
        {playlists.map((p) => {
          const on = picked.includes(p.id);
          return (
            <label key={p.id} className={"chip " + (on ? "chip--have" : "chip--neutral")}
              style={{ cursor: "pointer", textTransform: "none", gap: 6, display: "inline-flex", alignItems: "center" }}>
              <input type="checkbox" checked={on} style={{ margin: 0 }}
                onChange={() => setPicked((v) => on ? v.filter((x) => x !== p.id) : [...v, p.id])} />
              {p.name}
            </label>
          );
        })}
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <select aria-label="Merge into" value={targetId} style={{ fontSize: 13, minWidth: 180 }}
          onChange={(e) => setTargetId(e.target.value === "new" ? "new" : Number(e.target.value))}>
          <option value="new">New local playlist…</option>
          {locals.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        {targetId === "new" && (
          <input type="text" value={newName} placeholder="New playlist name" maxLength={120}
            aria-label="New playlist name" style={{ fontSize: 13, minWidth: 200 }}
            onChange={(e) => setNewName(e.target.value)} />
        )}
        <button className="btn btn-primary btn-md" disabled={!ready || merge.isPending}
          onClick={() => merge.mutate({ source_ids: picked, target })}>
          {merge.isPending ? "Merging…" : `Merge ${picked.length || ""}`.trim()}
        </button>
      </div>
      {err && <div style={{ color: "var(--err-fg)", fontSize: 12 }}>{err}</div>}
      {report && (
        <div style={{ fontSize: 12, borderTop: "1px solid var(--border)", paddingTop: 8 }}>
          <div style={{ marginBottom: 4 }}>
            <Link to={`/playlist?id=${report.target.id}`} style={{ color: "var(--accent-2)" }}>
              {report.target.name}
            </Link>
            {" — "}{mergeSummary(report.totals)}
          </div>
          {report.sources.map((s) => (
            <div key={s.id} style={{ color: "var(--muted)" }}>{s.name}: {mergeSummary(s)}</div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Playlists() {
  useTitle("Playlists");
  const qc = useQueryClient();
  const navigate = useNavigate();
  const status = useGrabberStatus();
  const [url, setUrl] = useState("");
  const [localName, setLocalName] = useState("");
  const [addErr, setAddErr] = useState("");
  const [browsing, setBrowsing] = useState<null | "spotify" | "navidrome">(null);
  const [op, setOp] = useState<null | "compare" | "merge">(null);

  const playlistsQ = useQuery({
    queryKey: ["playlists"],
    queryFn: () => api.get<{ playlists: Playlist[] }>("/api/playlists"),
    refetchInterval: 10_000,
  });

  // Cadence readiness — one fetch feeding both the strip above the grid and the
  // per-card badges. Failure is silent: these are extras, not the page.
  const readinessQ = useQuery({
    queryKey: ["run-readiness"],
    queryFn: () => api.get<Readiness>("/api/run/readiness"),
    staleTime: 60_000,
    retry: false,
  });
  const readiness = readinessQ.data;
  const readinessById = new Map((readiness?.playlists ?? []).map((p) => [p.id, p.counts]));

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

  /** Run this playlist at that cadence: the Run page reads its source from
   *  localStorage, so set it before navigating with the target deep-linked. */
  const runPlaylistAt = (playlistId: number, bpm: number) => {
    localStorage.setItem(RUN_SOURCE_KEY, `pl:${playlistId}`);
    navigate(`/run?bpm=${bpm}`);
  };

  return (
    <>
      <PageHeader
        title="Playlists"
        subtitle={
          playlists.length > 0
            ? <><span style={{ fontFamily: "var(--mono)", color: "var(--text)" }}>{playlists.length}</span> playlist{playlists.length === 1 ? "" : "s"} compared against your library</>
            : "Spotify and Navidrome playlists compared against your library."
        }
        actions={playlists.length >= 2 ? (
          <>
            <button className="btn btn-ghost btn-sm" aria-pressed={op === "compare"}
              onClick={() => setOp((o) => o === "compare" ? null : "compare")}>
              Compare…
            </button>
            <button className="btn btn-ghost btn-sm" aria-pressed={op === "merge"}
              onClick={() => setOp((o) => o === "merge" ? null : "merge")}>
              Merge…
            </button>
          </>
        ) : undefined}
      />

      {op && (
        <div className="card" style={{ marginBottom: 18 }}>
          <div className="section-label">
            <span>{op === "compare" ? "Compare two playlists" : "Merge playlists"}</span>
            <span className="section-hint">local-first — the sources are never modified</span>
          </div>
          {op === "compare"
            ? <ComparePanel playlists={playlists} />
            : <MergePanel playlists={playlists} onDone={invalidate} />}
        </div>
      )}

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

      {/* Cadence strip: one card per run preset with the library-wide runnable
          count. Absent (not a placeholder) while loading, so the grid below
          doesn't jump. */}
      {readiness && (
        <div className="cadence-strip">
          {readiness.presets.map((p) => (
            <Link key={p.bpm} to={`/cadence?bpm=${p.bpm}`} className="cadence-card"
              title={`${readiness.library[String(p.bpm)] ?? 0} library tracks runnable at ${p.name} (${p.bpm} BPM) within ±${readiness.stretch_limit_pct.toFixed(1)}%`}>
              <span className="cadence-card-name">{p.name}</span>
              <span className="cadence-card-bpm">{p.bpm}</span>
              <span className="cadence-card-count">{readiness.library[String(p.bpm)] ?? 0} ready</span>
            </Link>
          ))}
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
                  {readiness && readinessById.has(p.id) && (
                    <ReadinessBadges
                      counts={readinessById.get(p.id)!}
                      presets={readiness.presets}
                      stretchPct={readiness.stretch_limit_pct}
                      playlistId={p.id}
                      name={p.name}
                      onPick={runPlaylistAt}
                    />
                  )}
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
