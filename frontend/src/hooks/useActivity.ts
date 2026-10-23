import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { ActivityItem } from "../lib/types";

/** Polls /api/activity (loudness / waveform / lyrics / ISRC fills and Navidrome syncs)
 *  every 3 s while the tab is visible. Admin chrome only: players get a 403. */
export function useActivity(): ActivityItem[] {
  const [items, setItems] = useState<ActivityItem[]>([]);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      if (document.visibilityState === "hidden") return;
      try {
        const d = await api.get<{ items: ActivityItem[] }>("/api/activity");
        if (alive) setItems(d.items ?? []);
      } catch {
        /* transient — keep the last list */
      }
    };
    poll();
    const id = window.setInterval(poll, 3000);
    document.addEventListener("visibilitychange", poll);
    return () => {
      alive = false;
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", poll);
    };
  }, []);

  return items;
}
