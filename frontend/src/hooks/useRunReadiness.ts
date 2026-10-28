import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

/** Per-preset runnable counts for the library and for each playlist, from
 *  `GET /api/run/readiness`. One shape, one cache entry, read by the Playlists
 *  cadence strip, Home's run cards and the Run page's preset buttons. */
export interface Readiness {
  presets: Array<{ name: string; bpm: number }>;
  stretch_limit_pct: number;
  library: Record<string, number>;
  playlists: Array<{ id: number; name: string; counts: Record<string, number> }>;
}

export const RUN_READINESS_KEY = ["run-readiness"] as const;

/** How many tracks are runnable at `bpm`: from one playlist when `playlistId` is
 *  given, otherwise from the whole library. `null` when the numbers aren't in yet
 *  or the playlist isn't known, so callers can show nothing rather than a wrong 0. */
export function readyCount(r: Readiness | undefined, bpm: number, playlistId: number | null = null): number | null {
  if (!r) return null;
  if (playlistId == null) return r.library[String(bpm)] ?? 0;
  const pl = r.playlists.find((p) => p.id === playlistId);
  return pl ? pl.counts[String(bpm)] ?? 0 : null;
}

/** The shared readiness query. Failure is silent (`retry: false`): the counts are
 *  an extra, never the page. Pass `enabled: false` for roles the server refuses. */
export function useRunReadiness({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: RUN_READINESS_KEY,
    queryFn: () => api.get<Readiness>("/api/run/readiness"),
    staleTime: 60_000,
    retry: false,
    enabled,
  });
}
