import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { PlaylistTrack, RelatedTrack } from "../lib/types";
import { useGrabberStatus } from "../hooks/useGrabberStatus";
import { useSuggestionQueue } from "../hooks/useSuggestionQueue";
import { PreviewButton } from "./trackBits";

const norm = (s: string | null | undefined) => (s || "").trim().toLowerCase();

/** Top artists by frequency across the playlist's tracks (prefers the matched
 *  library artist, falls back to the source artist). */
export function seedArtists(tracks: PlaylistTrack[], limit = 5): string[] {
  const counts = new Map<string, number>();
  for (const t of tracks) {
    const a = (t.local_artist || t.artist || "").trim();
    if (!a) continue;
    counts.set(a, (counts.get(a) ?? 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, limit).map(([a]) => a);
}

/** Admin-only "Suggestions" panel for a *local* playlist: Deezer artist radio
 *  seeded from the playlist's own most-frequent artists. In-library matches add
 *  straight to the playlist; missing tracks use the grabber-gated download flow
 *  (mirrors QueueSimilar). Tracks already in the playlist are filtered out. */
export default function PlaylistSuggestions({ playlistId, tracks }: {
  playlistId: string;
  tracks: PlaylistTrack[];
}) {
  const qc = useQueryClient();
  const grabber = useGrabberStatus();
  const grabberEnabled = grabber.data?.enabled === true;
  const grab = useSuggestionQueue();

  const seeds = useMemo(() => seedArtists(tracks), [tracks]);
  const [seed, setSeed] = useState<string | null>(null);
  const activeSeed = seed && seeds.includes(seed) ? seed : seeds[0] ?? null;

  // Rows already in the playlist: by matched file path, and by title+artist so
  // a suggestion that's present but unmatched (missing/queued) is still hidden.
  const existingPaths = useMemo(
    () => new Set(tracks.map((t) => t.matched_file_path).filter(Boolean) as string[]),
    [tracks],
  );
  const existingKeys = useMemo(
    () => new Set(tracks.map((t) => `${norm(t.title)}|${norm(t.local_artist || t.artist)}`)),
    [tracks],
  );

  const [added, setAdded] = useState<Set<string>>(new Set());

  const tracksQ = useQuery({
    queryKey: ["related-tracks", activeSeed],
    queryFn: () => api.get<{ tracks: RelatedTrack[] }>(
      `/api/related/tracks?name=${encodeURIComponent(activeSeed!)}`),
    enabled: !!activeSeed,
    staleTime: Infinity,
  });

  const addLocal = useMutation({
    mutationFn: (path: string) =>
      api.post<{ ok: boolean; error?: string }>(`/api/playlists/${playlistId}/tracks`, { path }),
    onSuccess: (_r, path) => {
      setAdded((s) => new Set(s).add(path));
      qc.invalidateQueries({ queryKey: ["playlist-tracks", playlistId] });
      qc.invalidateQueries({ queryKey: ["playlists"] });
    },
  });

  const suggestions = (tracksQ.data?.tracks ?? []).filter((t) => {
    if (t.file_path && existingPaths.has(t.file_path)) return false;
    if (existingKeys.has(`${norm(t.title)}|${norm(t.artist)}`)) return false;
    return true;
  });

  if (seeds.length === 0) return null;  // nothing to seed from (empty playlist)

  return (
    <div className="card" style={{ marginTop: 20 }}>
      <div className="player-queue-head" style={{ borderBottom: "1px solid var(--border)", margin: "-16px -16px 12px", padding: "12px 16px" }}>
        <span>Suggestions · Deezer</span>
      </div>

      {seeds.length > 1 && (
        <div className="filter-pills" style={{ marginBottom: 14, width: "fit-content", flexWrap: "wrap" }}>
          {seeds.map((s) => (
            <button
              key={s}
              className={"filter-pill" + (s === activeSeed ? " active" : "")}
              onClick={() => setSeed(s)}
              title={`Suggestions similar to ${s}`}
            >
              {s}
            </button>
          ))}
        </div>
      )}

      {tracksQ.isLoading ? (
        <div style={{ padding: 14, fontSize: 12, color: "var(--muted)", textAlign: "center" }}>Loading…</div>
      ) : suggestions.length === 0 ? (
        <div style={{ padding: 14, fontSize: 12, color: "var(--muted)", textAlign: "center" }}>
          {tracksQ.isError
            ? "Couldn't reach Deezer for suggestions."
            : `No new suggestions for ${activeSeed}.`}
        </div>
      ) : (
        <div className="player-queue-list" style={{ padding: 0 }}>
          {suggestions.map((t) => {
            const justAdded = !!t.file_path && added.has(t.file_path);
            return (
              <div key={t.dz_track_id} className="player-queue-row">
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ display: "block", fontSize: 12, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.title}</span>
                  <span style={{ display: "block", fontSize: 11, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {t.artist}
                    {t.in_library && t.bpm != null && <span style={{ fontFamily: "var(--mono)" }}> · {Math.round(t.bpm)} BPM</span>}
                  </span>
                </span>
                <span className="player-queue-actions" style={{ alignItems: "center", gap: 6 }}>
                  {t.preview_url && (
                    <PreviewButton track={{ dz_track_id: t.dz_track_id, title: t.title, artist: t.artist, preview_url: t.preview_url }} />
                  )}
                  {justAdded ? (
                    <span style={{ fontSize: 11, color: "var(--muted)", flexShrink: 0 }}>✓ added</span>
                  ) : t.in_library && t.file_path ? (
                    <button
                      className="btn btn-soft btn-sm"
                      disabled={addLocal.isPending && addLocal.variables === t.file_path}
                      onClick={() => addLocal.mutate(t.file_path!)}
                      title="In your library — add to this playlist"
                    >Add</button>
                  ) : t.queued ? (
                    <span className="chip chip--queued" style={{ flexShrink: 0 }}>↓ queued</span>
                  ) : grabberEnabled ? (
                    <button
                      className="btn btn-soft btn-sm"
                      disabled={grab.isPending && grab.variables?.dz_track_id === t.dz_track_id}
                      onClick={() => grab.mutate({
                        dz_track_id: t.dz_track_id, title: t.title, artist: t.artist,
                        album: t.album, duration_ms: t.duration_ms, cover_url: t.cover_url,
                      })}
                      title="Not in your library — add to the download queue"
                    >Grab</button>
                  ) : (
                    <span style={{ fontSize: 11, color: "var(--muted)", flexShrink: 0 }} title="Enable the grabber to download missing tracks">not in library</span>
                  )}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
