import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { GrabberStatus } from "../lib/types";

/** Polls the consolidated grabber status (enabled + Spotify + queue counts). */
export function useGrabberStatus() {
  return useQuery({
    queryKey: ["grabber-status"],
    queryFn: () => api.get<GrabberStatus>("/api/grabber/status"),
    refetchInterval: 5000,
    staleTime: 3000,
  });
}
