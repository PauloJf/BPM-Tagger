import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";

/** One Deezer track to enqueue via /api/suggestions/queue. */
export interface QueueTrackInput {
  dz_track_id: string;
  title: string;
  artist: string;
  album?: string;
  duration_ms?: number | null;
  cover_url?: string;
  suggestion_id?: number;  // set for stored suggestions so the row is marked queued
}

/** Shared "add a suggested/related track to the grab queue" mutation. Used by
 *  the Suggestions page and the Related panel; invalidates every view a new
 *  queue item can change. */
export function useSuggestionQueue() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (t: QueueTrackInput) =>
      api.post<{ ok: boolean; id?: number; error?: string }>("/api/suggestions/queue", {
        dz_track_id: t.dz_track_id,
        title: t.title,
        artist: t.artist,
        album: t.album,
        duration_ms: t.duration_ms,
        cover_url: t.cover_url,
        suggestion_id: t.suggestion_id,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["suggestions"] });
      qc.invalidateQueries({ queryKey: ["queue"] });
      qc.invalidateQueries({ queryKey: ["grabber-status"] });
      qc.invalidateQueries({ queryKey: ["related-tracks"] });
      qc.invalidateQueries({ queryKey: ["suggestion-artist-tracks"] });
    },
  });
}
