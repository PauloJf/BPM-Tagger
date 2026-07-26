import { useState } from "react";
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
  const tracks = tracksQ.data?.tracks ?? [];
  // Playback works off the rows currently shown, so a tab filter is respected:
  // on the Have tab you play what you see. `tracks` is already server-filtered
  // by `tab`, so no extra filtering is needed here.
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

      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16, flexWrap: "wrap" }}>
        <div className="filter-pills" style={{ width: "fit-content" }}>
          {tabs.map((t) => (
            <button key={t.key} className={"filter-pill" + (tab === t.key ? " active" : "")} onClick={() => setTab(t.key)}>
              {t.label}
            </button>
          ))}
        </div>
        <div style={{ flex: 1 }} />
        <ArtToggle show={showArt} onToggle={toggleArt} />
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
              <div className={"pl-track-row" + (showArt ? " pl-track-row--art" : "") + (t.removed_at ? " pl-track-row--removed" : "")} key={t.id}>
                <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--muted)" }}>
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
                  {role === "admin" && (inLib || isLocal) && (
                    <div style={{ display: "flex", alignItems: "center", gap: 2 }}>
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
