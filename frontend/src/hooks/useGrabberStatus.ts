import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { GrabberStatus } from "../lib/types";

/** Polls the consolidated grabber status (enabled + Spotify + queue counts). */
export function useGrabberStatus() {
  return useQuery({
    queryKey: ["grabber-status"],
    queryFn: () => api.get<GrabberStatus>("/api/grabber/status"),
    // Poll faster while downloads are active, slower when idle.
    refetchInterval: (query) => ((query.state.data as GrabberStatus | undefined)?.active?.length ? 1500 : 4000),
    staleTime: 1000,
  });
}
