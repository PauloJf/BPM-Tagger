import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Playlist, PlaylistTrack } from "../lib/types";
import { basename } from "../lib/paths";
import { usePlayer, type PlayerTrack } from "../lib/player";
import { useTitle } from "../hooks/useTitle";
import { useGrabberStatus } from "../hooks/useGrabberStatus";
import { useAuth } from "../lib/auth";
import PlaylistSuggestions from "../components/PlaylistSuggestions";
import PlaylistStats from "../components/PlaylistStats";
import AddToPlaylistMenu from "../components/AddToPlaylistMenu";
import { ArtPlaceholder, ArtToggle, Cover, PlaylistCover, RemoteCover, useArtwork } from "../components/Artwork";
import { ImagePicker } from "../components/ImagePicker";
import { apiUpload } from "../lib/api";

const PlayIcon = () => <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" style={{ marginRight: 4 }}><polygon points="6,4 20,12 6,20" /></svg>;
const ShuffleIcon = () => <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ marginRight: 4 }}><path d="M16 3h5v5M4 20L21 3M21 16v5h-5M15 15l6 6M4 4l5 5" /></svg>;
const AddIcon = () => <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" style={{ marginRight: 4 }}><path d="M12 5v14M5 12h14" /></svg>;

/** The rows of a playlist that can actually be played: matched to a local file.
 *  Everything a PlayerTrack needs already rides along on a 'have' row — including
 *  the library track's loudness, which drives volume levelling. Exported for the
 *  component test. */
export function playableTracks(tracks: PlaylistTrack[]): PlayerTrack[] {
  return tracks
    .filter((t) => t.derived_status === "have" && !!t.matched_file_path)
    .map((t) => ({
      path: t.matched_file_path!,
      title: t.title || basename(t.matched_file_path!),
      artist: t.local_artist || t.artist || "",
      bpm: t.local_bpm ?? null,
      loudnessLufs: t.local_loudness_lufs ?? null,
    }));
}

// ── in-playlist sort / search / duplicates (all client-side) ────────────────

export type SortKey = "position" | "title" | "artist" | "bpm" | "duration";

const SORT_OPTIONS: Array<{ key: SortKey; label: string }> = [
  { key: "position", label: "Playlist order" },
  { key: "title", label: "Title" },
  { key: "artist", label: "Artist" },
  { key: "bpm", label: "BPM" },
  { key: "duration", label: "Length" },
];

/** Library value first, source metadata second — a matched row's own tags are
 *  the truth, and the source's are all an unmatched row has. */
const trackArtist = (t: PlaylistTrack) => t.local_artist || t.artist || "";
const trackAlbum = (t: PlaylistTrack) => t.local_album || t.album || "";
const trackDuration = (t: PlaylistTrack) => t.local_duration_ms ?? t.duration_ms ?? null;

/** Ascending, with nulls last regardless of direction — a track with no BPM
 *  sorts to the bottom rather than pretending to be 0. */
function nullsLast(a: number | null, b: number | null): number {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  return a - b;
}

const byText = (get: (t: PlaylistTrack) => string) => (a: PlaylistTrack, b: PlaylistTrack) =>
  get(a).localeCompare(get(b), undefined, { sensitivity: "base" }) || a.position - b.position;

const comparators: Record<Exclude<SortKey, "position">, (a: PlaylistTrack, b: PlaylistTrack) => number> = {
  title: byText((t) => t.title || ""),
  artist: byText(trackArtist),
  bpm: (a, b) => nullsLast(a.local_bpm ?? null, b.local_bpm ?? null) || a.position - b.position,
  duration: (a, b) => nullsLast(trackDuration(a), trackDuration(b)) || a.position - b.position,
};

/** Case-insensitive substring over title / artist / album, both the source's
 *  metadata and the matched library track's. Exported for the component test. */
export function matchesSearch(t: PlaylistTrack, q: string): boolean {
  return [t.title, t.artist, t.album, t.local_artist, t.local_album]
    .some((v) => (v || "").toLowerCase().includes(q));
}

/** Group live rows by the library file they point at and by non-empty ISRC;
 *  every group of two or more is a duplicate cluster. Returns the row ids in
 *  some cluster. Exported for the component test. */
export function dupRowIds(tracks: PlaylistTrack[]): Set<number> {
  const groups = new Map<string, number[]>();
  for (const t of tracks) {
    if (t.derived_status === "removed") continue;   // tombstones aren't membership
    for (const key of [t.matched_file_path && `p:${t.matched_file_path}`,
                       t.isrc && `i:${t.isrc}`]) {
      if (!key) continue;
      const g = groups.get(key);
      if (g) g.push(t.id); else groups.set(key, [t.id]);
    }
  }
  const ids = new Set<number>();
  for (const g of groups.values()) {
    if (g.length > 1) g.forEach((id) => ids.add(id));
  }
  return ids;
}

/** How many distinct duplicate clusters there are (a row duplicated by both its
 *  path and its ISRC is still one problem, so clusters are merged by row id). */
export function countDupClusters(tracks: PlaylistTrack[]): number {
  const groups = new Map<string, number[]>();
  for (const t of tracks) {
    if (t.derived_status === "removed") continue;
    for (const key of [t.matched_file_path && `p:${t.matched_file_path}`,
                       t.isrc && `i:${t.isrc}`]) {
      if (!key) continue;
      const g = groups.get(key);
      if (g) g.push(t.id); else groups.set(key, [t.id]);
    }
  }
  // Union the overlapping groups so one track listed twice — matching on both
  // path and ISRC — counts once, not twice.
  const seen = new Set<number>();
  let clusters = 0;
  for (const g of [...groups.values()].filter((g) => g.length > 1)) {
    if (g.every((id) => seen.has(id))) continue;
    g.forEach((id) => seen.add(id));
    clusters++;
  }
  return clusters;
}

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

const PencilIcon = ({ size = 12 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
  </svg>
);

/** Click-to-edit text field: a pencil beside the read-only rendering swaps in an
 *  input with Enter-to-save / Escape-to-cancel plus explicit buttons (the pencil
 *  alone isn't discoverable enough on touch). The draft is seeded when the editor
 *  opens, so a background refetch can't yank half-typed text away. */
function InlineEdit({ value, noun, maxLength, multiline, saving, onSave, children }: {
  value: string;
  noun: string;
  maxLength: number;
  multiline?: boolean;
  saving?: boolean;
  onSave: (v: string) => void;
  children: React.ReactNode;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);

  if (!editing) {
    return (
      <span style={{ display: "inline-flex", alignItems: "center", gap: 6, minWidth: 0 }}>
        {children}
        <button
          className="btn btn-bare btn-sm"
          style={{ padding: "2px 4px", color: "var(--muted)", flexShrink: 0 }}
          aria-label={`Edit ${noun}`}
          title={`Edit ${noun}`}
          onClick={() => { setDraft(value); setEditing(true); }}
        >
          <PencilIcon />
        </button>
      </span>
    );
  }

  const commit = () => { onSave(draft.trim()); setEditing(false); };
  const Field = multiline ? "textarea" : "input";
  return (
    <span style={{ display: "inline-flex", alignItems: multiline ? "flex-start" : "center", gap: 6, flexWrap: "wrap" }}>
      <Field
        autoFocus
        value={draft}
        maxLength={maxLength}
        aria-label={`Playlist ${noun}`}
        rows={multiline ? 2 : undefined}
        style={{ fontSize: 13, minWidth: 220, ...(multiline ? { resize: "vertical" as const } : {}) }}
        onChange={(e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setDraft(e.target.value)}
        onKeyDown={(e: React.KeyboardEvent) => {
          // Enter saves a single-line field; a textarea keeps Enter for newlines.
          if (e.key === "Enter" && !multiline) { e.preventDefault(); commit(); }
          if (e.key === "Escape") { e.preventDefault(); setEditing(false); }
        }}
      />
      <button className="btn btn-primary btn-sm" disabled={saving} onClick={commit}>Save</button>
      <button className="btn btn-bare btn-sm" onClick={() => setEditing(false)}>Cancel</button>
    </span>
  );
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
  const { role } = useAuth();
  const player = usePlayer();
  const [tab, setTab] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("position");
  const [search, setSearch] = useState("");
  const [dupsOnly, setDupsOnly] = useState(false);
  const [dragIdx, setDragIdx] = useState<number | null>(null);
  const [dragOverIdx, setDragOverIdx] = useState<number | null>(null);
  const [showArt, toggleArt] = useArtwork();
  const [pickerOpen, setPickerOpen] = useState(false);
  // Bumped after a cover set/delete so the browser refetches the (max-age'd)
  // cover endpoint instead of showing the old image.
  const [coverV, setCoverV] = useState(0);

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
  // Grabs the missing tracks via the download providers. The route keeps its
  // original name (it's the download queue); only the button says "Download".
  const downloadMissing = useMutation({
    mutationFn: () => api.post<{ enqueued: number }>(`/api/playlists/${id}/enqueue-missing`),
    onSuccess: invalidate,
  });
  const removeTrack = useMutation({
    mutationFn: (ptId: number) => api.del(`/api/playlists/${id}/tracks/${ptId}`),
    onSuccess: invalidate,
  });
  // Rename / description. Also invalidates the Run page's source picker, which
  // lists playlists by name.
  const patchMeta = useMutation({
    mutationFn: (body: { name?: string; description?: string }) =>
      api.patch(`/api/playlists/${id}`, body),
    onSuccess: () => { invalidate(); qc.invalidateQueries({ queryKey: ["run-playlists"] }); },
  });

  const pl = tracksQ.data?.playlist;
  useTitle(pl?.name || "Playlist");
  const allTracks = tracksQ.data?.tracks ?? [];

  // Duplicate clusters, over live rows only: the same library file listed twice,
  // or two rows sharing an ISRC. Informational on synced playlists (the source
  // owns membership); on a local one the per-row ✕ is the fix.
  const dupIds = useMemo(() => dupRowIds(allTracks), [allTracks]);
  const dupClusters = useMemo(() => countDupClusters(allTracks), [allTracks]);

  // Sort + search are client-side over the rows the server already filtered by
  // tab — the endpoint returns the whole playlist unpaginated, so there's
  // nothing to fetch. Everything downstream (playback, duplicates-only, the
  // reorder gate) reads `tracks`, so what you see is what you get.
  const tracks = useMemo(() => {
    let rows = allTracks;
    if (dupsOnly) rows = rows.filter((t) => dupIds.has(t.id));
    const q = search.trim().toLowerCase();
    if (q) rows = rows.filter((t) => matchesSearch(t, q));
    return sortKey === "position" ? rows : [...rows].sort(comparators[sortKey]);
  }, [allTracks, dupsOnly, dupIds, search, sortKey]);

  // Playback works off the rows currently shown, so the tab filter, the search
  // and the sort are all respected: on the Have tab you play what you see.
  const playable = playableTracks(tracks);
  const canPlay = playable.length > 0;
  // Surface the count only when it differs from what's listed — a 50-track
  // Spotify playlist with 12 matched must read "Play (12)", or the button looks
  // broken; on the Have tab the numbers agree and a count would be noise.
  const playLabel = canPlay && playable.length !== tracks.length ? ` (${playable.length})` : "";
  const noPlayReason = "No tracks in this playlist are in your library yet";
  const spotifyConnected = status.data?.spotify?.connected === true;
  const grabberEnabled = status.data?.enabled === true;
  const isSpotify = pl?.source === "spotify";
  const isLocal = pl?.source === "local";
  const canSync = pl?.source === "navidrome" || (isSpotify && spotifyConnected);
  // Queuing missing tracks works for any synced source now (Phase 5) — a Navidrome
  // playlist's missing tracks are grabbed by metadata via the shared enqueue helper.
  // Gated on the grabber being enabled; Spotify additionally needs a live connection.
  const canQueueMissing = !isLocal && grabberEnabled && (pl?.missing_count ?? 0) > 0;
  // Rename is Local-only (sync owns a mirror's name); the description is ours on
  // any source. Both are admin-only, like the rest of playlist management.
  const canRename = role === "admin" && isLocal && !!pl;
  const canEditDescription = role === "admin" && !!pl;
  const description = pl?.description || "";
  // Local playlists have no image_url — their art comes from the cover endpoint
  // (a custom pick, else an auto-collage of their own tracks), which 404s into
  // the ♪ placeholder when there's neither.
  const canSetCover = role === "admin" && isLocal;
  // Reordering is only meaningful over the untouched playlist order: a sorted,
  // searched or duplicates-only view is a projection, and dropping a row into
  // it has no single obvious meaning. So the drag handles and ↑/↓ appear only
  // on the plain All tab of a local playlist, where the rows on screen ARE the
  // playlist. (The endpoint takes the complete order, which is exactly what
  // `tracks` is in that state.)
  const canReorder = role === "admin" && isLocal && tab === ""
    && sortKey === "position" && !search.trim() && !dupsOnly;

  const reorder = useMutation({
    mutationFn: (order: number[]) => api.post(`/api/playlists/${id}/reorder`, { order }),
    // Either way the server is the truth: on success to pick up anything else
    // that changed, on failure to roll the optimistic order back.
    onSettled: invalidate,
  });

  function moveRow(from: number, to: number) {
    if (from === to || to < 0 || to >= tracks.length) return;
    const next = [...tracks];
    next.splice(to, 0, ...next.splice(from, 1));
    // Paint the new order immediately; the row positions are renumbered so the
    // displayed track numbers don't jump around while the POST is in flight.
    qc.setQueryData(["playlist-tracks", id, tab],
      (old: { playlist: Playlist; tracks: PlaylistTrack[] } | undefined) =>
        old ? { ...old, tracks: next.map((t, i) => ({ ...t, position: i })) } : old);
    reorder.mutate(next.map((t) => t.id));
  }

  async function applyCover(pick: { url?: string; file?: File }) {
    if (pick.url) await api.post(`/api/playlists/${id}/cover`, { url: pick.url });
    else if (pick.file) await apiUpload(`/api/playlists/${id}/cover`, pick.file);
    setCoverV(Date.now());
    setPickerOpen(false);
  }

  async function resetCover() {
    await api.del(`/api/playlists/${id}/cover`);
    setCoverV(Date.now());
    setPickerOpen(false);
  }

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
        <button
          className="btn btn-primary btn-sm"
          disabled={!canPlay}
          title={canPlay ? "Play this playlist's library tracks" : noPlayReason}
          onClick={() => player.playQueue(playable, 0, { shuffle: false })}
        >
          <PlayIcon />
          Play{playLabel}
        </button>
        <button
          className="btn btn-ghost btn-sm"
          disabled={!canPlay}
          title={canPlay ? "Play this playlist's library tracks in a random order" : noPlayReason}
          onClick={() => player.playQueue(playable, 0, { shuffle: true })}
        >
          <ShuffleIcon />
          Shuffle{playLabel}
        </button>
        <button
          className="btn btn-bare btn-sm"
          disabled={!canPlay}
          title={canPlay ? "Append this playlist's library tracks to the current queue" : noPlayReason}
          onClick={() => player.enqueueMany(playable)}
        >
          <AddIcon />
          Add to queue
        </button>
        {canQueueMissing && (
          <button
            className="btn btn-soft btn-sm"
            disabled={downloadMissing.isPending || (isSpotify && !spotifyConnected)}
            title={isSpotify && !spotifyConnected ? "Connect Spotify to download missing tracks" : "Grab the missing tracks via the download providers"}
            onClick={() => downloadMissing.mutate()}
          >
            {downloadMissing.isPending ? "Downloading…" : `Download missing (${pl?.missing_count})`}
          </button>
        )}
        {role === "admin" && (pl?.have_count ?? 0) > 0 && (
          <AddToPlaylistMenu
            importFrom={Number(id)}
            heading="Copy have-tracks to…"
            label="Add all to playlist…"
            title="Copy this playlist's library tracks into a local playlist"
            className="btn btn-soft btn-sm"
            iconSize={13}
          />
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
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6 }}>
          {isLocal ? (
            <PlaylistCover id={id} v={coverV} size={72} />
          ) : pl?.image_url ? (
            <img src={pl.image_url} alt="" className="pl-cover" style={{ width: 72, height: 72 }} referrerPolicy="no-referrer" />
          ) : null}
          {canSetCover && (
            <button className="btn btn-bare btn-sm" style={{ fontSize: 11, padding: "2px 4px", color: "var(--muted)" }}
              onClick={() => setPickerOpen(true)}>
              Set cover…
            </button>
          )}
        </div>
        <div style={{ minWidth: 0 }}>
          <h1 style={{ fontSize: 24, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 8 }}>
            {/* Renaming is Local-only — a synced playlist's name is rewritten by
                its next sync, so the API refuses it and the control stays hidden. */}
            {canRename ? (
              <InlineEdit
                value={pl!.name}
                noun="name"
                maxLength={200}
                saving={patchMeta.isPending}
                onSave={(name) => { if (name) patchMeta.mutate({ name }); }}
              >
                <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{pl!.name}</span>
              </InlineEdit>
            ) : (pl?.name || "…")}
          </h1>
          {/* Description works on every source (sync never touches the column). */}
          {(canEditDescription || description) && (
            <div style={{ fontSize: 13, color: "var(--muted)", marginBottom: 8, maxWidth: 620, whiteSpace: "pre-wrap" }}>
              {canEditDescription ? (
                <InlineEdit
                  value={description}
                  noun="description"
                  maxLength={1000}
                  multiline
                  saving={patchMeta.isPending}
                  onSave={(d) => patchMeta.mutate({ description: d })}
                >
                  <span style={description ? undefined : { fontStyle: "italic", opacity: 0.7 }}>
                    {description || "Add a description"}
                  </span>
                </InlineEdit>
              ) : description}
            </div>
          )}
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

      {/* Library-side rollup of the matched tracks. Skipped for a player session:
          the endpoint is outside _PLAYER_ALLOWED (default-deny), so asking would
          only earn a 403. */}
      {role !== "player" && <PlaylistStats playlistId={id} />}

      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16, flexWrap: "wrap" }}>
        <div className="filter-pills" style={{ width: "fit-content" }}>
          {tabs.map((t) => (
            <button key={t.key} className={"filter-pill" + (tab === t.key ? " active" : "")} onClick={() => setTab(t.key)}>
              {t.label}
            </button>
          ))}
        </div>
        <div style={{ flex: 1 }} />
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search this playlist…"
          aria-label="Search this playlist"
          style={{ fontSize: 12, minWidth: 170 }}
        />
        <select
          value={sortKey}
          onChange={(e) => setSortKey(e.target.value as SortKey)}
          aria-label="Sort tracks"
          style={{ fontSize: 12 }}
        >
          {SORT_OPTIONS.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
        </select>
        <ArtToggle show={showArt} onToggle={toggleArt} />
      </div>

      {dupClusters > 0 && (
        <div
          className="card"
          style={{ marginBottom: 12, padding: "8px 12px", display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", fontSize: 12 }}
        >
          <span title={isLocal
            ? "Two rows point at the same library file (or share an ISRC). Remove the extras with the ✕ on the row."
            : "Two rows point at the same library file (or share an ISRC). This playlist's membership belongs to its source, so fix it there."}>
            ⧉ {dupClusters} duplicate{dupClusters === 1 ? "" : "s"} in this playlist
            {!isLocal && <span style={{ color: "var(--muted)" }}> — the source owns this playlist's membership</span>}
          </span>
          <div style={{ flex: 1 }} />
          <button className="btn btn-ghost btn-sm" aria-pressed={dupsOnly} onClick={() => setDupsOnly((v) => !v)}>
            {dupsOnly ? "Show all tracks" : "Show duplicates only"}
          </button>
        </div>
      )}

      <div className="tracks-table">
        {tracks.length === 0 ? (
          <div className="tracks-row-empty">
            {tracksQ.isLoading ? "Loading…"
              // Distinguish "this playlist is empty" from "your search hid
              // everything" — otherwise a typo reads as a missing playlist.
              : allTracks.length > 0 ? "No tracks match your search."
              : isLocal ? "No tracks yet — add tracks from a track page or the library “add to playlist” button."
              : "No tracks."}
          </div>
        ) : (
          tracks.map((t, i) => {
            // 'have' rows are matched to a local file → link into the library and
            // show its real BPM. Missing/queued/removed rows stay plain text.
            const inLib = t.derived_status === "have" && !!t.matched_file_path;
            const label = t.title || "this track";
            const artist = trackArtist(t);
            const album = trackAlbum(t);
            const albumArtist = t.local_album_artist || album || artist;
            const dur = fmtDur(trackDuration(t));
            const plays = inLib ? (t.local_play_count ?? 0) : 0;
            const meta = [
              inLib && t.local_bpm != null ? `${Math.round(t.local_bpm)} BPM` : "",
              dur,
              plays > 0 ? `${plays} play${plays === 1 ? "" : "s"}` : "",
            ].filter(Boolean);
            return (
              <div
                key={t.id}
                className={"pl-track-row"
                  + (showArt ? " pl-track-row--art" : "")
                  + (t.removed_at ? " pl-track-row--removed" : "")
                  + (dragIdx === i ? " dragging" : "")
                  + (dragOverIdx === i && dragIdx !== null && dragIdx !== i ? " drag-over" : "")}
                draggable={canReorder}
                onDragStart={canReorder ? (e) => { setDragIdx(i); e.dataTransfer.effectAllowed = "move"; } : undefined}
                onDragOver={canReorder ? (e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; if (dragOverIdx !== i) setDragOverIdx(i); } : undefined}
                onDrop={canReorder ? (e) => { e.preventDefault(); if (dragIdx !== null) moveRow(dragIdx, i); setDragIdx(null); setDragOverIdx(null); } : undefined}
                onDragEnd={canReorder ? () => { setDragIdx(null); setDragOverIdx(null); } : undefined}
              >
                <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--muted)", display: "flex", alignItems: "center", gap: 4 }}>
                  {canReorder && <span className="player-queue-grip" aria-hidden title="Drag to reorder">⠿</span>}
                  {String(t.position + 1).padStart(2, "0")}
                </span>
                {/* A matched row shows the real file's embedded art; an unmatched
                    one falls back to whatever the source gave us, then to ♪ — so
                    the column is always the same width and the rows stay aligned. */}
                {showArt && (
                  inLib ? <Cover path={t.matched_file_path!} size={38} />
                    : t.cover_url ? <RemoteCover url={t.cover_url} size={38} />
                    : <ArtPlaceholder size={38} />
                )}
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", display: "flex", alignItems: "center", gap: 6 }}>
                    {inLib ? (
                      <Link data-testid="pl-title" to={`/track?path=${encodeURIComponent(t.matched_file_path!)}`} style={{ color: "inherit", textDecoration: "none", overflow: "hidden", textOverflow: "ellipsis" }} title="Open the track page">
                        {t.title}
                      </Link>
                    ) : (
                      <span data-testid="pl-title" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{t.title}</span>
                    )}
                    {!!t.is_new && !t.removed_at && <span className="chip chip--new" title="Added since you last viewed">✦ new</span>}
                    {dupIds.has(t.id) && (
                      <span className="chip chip--neutral" title="Another row in this playlist points at the same track">⧉ dup</span>
                    )}
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
                  {/* BPM · length · plays, in the row's existing quiet metadata
                      line. Plays only on a matched row (the count is the library
                      track's) and only once it's non-zero, so an unplayed library
                      doesn't grow a column of "0 plays". */}
                  {meta.length > 0 && (
                    <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)", whiteSpace: "nowrap" }}>
                      {meta.join(" · ")}
                    </span>
                  )}
                  {role === "admin" && (inLib || isLocal) && (
                    <div style={{ display: "flex", alignItems: "center", gap: 2 }}>
                      {/* Touch and keyboard equivalent of the drag handle — HTML5
                          drag doesn't exist on a phone, and this is the testable
                          path (both compute the same full-order payload). */}
                      {canReorder && (
                        <>
                          <button
                            className="btn btn-bare btn-sm"
                            style={{ padding: "2px 4px", color: "var(--muted)" }}
                            disabled={i === 0 || reorder.isPending}
                            aria-label={`Move ${label} up`}
                            title="Move up"
                            onClick={() => moveRow(i, i - 1)}
                          >↑</button>
                          <button
                            className="btn btn-bare btn-sm"
                            style={{ padding: "2px 4px", color: "var(--muted)" }}
                            disabled={i === tracks.length - 1 || reorder.isPending}
                            aria-label={`Move ${label} down`}
                            title="Move down"
                            onClick={() => moveRow(i, i + 1)}
                          >↓</button>
                        </>
                      )}
                      {inLib && (
                        <AddToPlaylistMenu
                          path={t.matched_file_path!}
                          className="btn btn-bare btn-sm"
                          style={{ padding: "2px 4px", color: "var(--muted)" }}
                          iconSize={13}
                        />
                      )}
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
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Related-track suggestions — local playlists only, and admin-only since
          the add endpoint is admin-scoped. */}
      {isLocal && role === "admin" && tracks.length > 0 && (
        <PlaylistSuggestions playlistId={id} tracks={tracks} />
      )}

      {pickerOpen && (
        <ImagePicker
          kind="album"
          title={`Playlist cover — ${pl?.name || ""}`}
          initialQuery={pl?.name || ""}
          onPick={applyCover}
          // Always offered: clearing a cover that was never set is harmless and
          // it's what takes a playlist back to its auto-collage.
          onReset={resetCover}
          onClose={() => setPickerOpen(false)}
        />
      )}
    </>
  );
}
