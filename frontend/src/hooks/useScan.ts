import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import type { Progress } from "../lib/types";

export type ScanState = "idle" | "analysing" | "paused" | "stopping";

function deriveState(d: Progress): ScanState {
  if (d.is_stopping) return "stopping";
  if (!d.is_scanning) return "idle";
  if (d.is_paused) return "paused";
  return "analysing";
}

/** Polls /api/progress every 2s and exposes scan control actions.
 *  Ports the vanilla-JS scan-controls logic from the Jinja base template. */
export function useScan() {
  const [state, setState] = useState<ScanState>("idle");

  const poll = useCallback(async () => {
    try {
      const d = await api.get<Progress>("/api/progress");
      setState(deriveState(d));
    } catch {
      /* transient — keep last state */
    }
  }, []);

  const act = useCallback(
    async (action: "start" | "pause" | "resume" | "stop") => {
      try {
        await api.post(`/api/scan/${action}`, {});
      } catch {
        /* ignore */
      }
      poll();
    },
    [poll],
  );

  useEffect(() => {
    poll();
    const id = window.setInterval(poll, 2000);
    return () => window.clearInterval(id);
  }, [poll]);

  return { state, act };
}
