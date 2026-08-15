import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import type { RunQueueResponse } from "../lib/types";
import {
  cachedPaths, estimateSize, fmtBytes, offlineSupported, pinnedRunQueue,
  pinRunQueue, preloadMany, reconcileIndex, type PreloadProgress,
} from "../lib/offline";

// "Prepare offline": per-preset download chips under the Run presets grid.
//
// Each chip builds a queue for its preset's BPM (pinning the exact server
// response — /api/run/queue randomizes every build, so the preloaded audio is
// only guaranteed to match a queue that was saved alongside it), estimates the
// download with HEAD requests, asks for one confirming tap, then downloads the
// tracks into the offline cache. startRun falls back to the pinned queue when
// the server is unreachable, and the service worker serves the cached bytes —
// so a preset prepared at home still runs in a network dead zone.
//
// Rendered only when the Cache API exists (secure contexts: https/localhost);
// over plain http the whole feature is unavailable and the chips hide.

type Phase = "idle" | "busy" | "confirm" | "downloading" | "error";

interface ChipState {
  phase: Phase;
  // confirm: the built queue waiting for the confirming tap + its estimate
  pending?: RunQueueResponse;
  estBytes?: number;
  progress?: PreloadProgress;
  // how much of the pinned queue is actually cached (ready = cached === total)
  cached: number;
  total: number;
  error?: string;
}

const BLANK: ChipState = { phase: "idle", cached: 0, total: 0 };

export default function PrepareOffline({ presets, scope, count }: {
  presets: { name: string; bpm: number }[];
  scope: string;    // the same &playlist=… suffix startRun uses ("" = library)
  count: number;    // run_preload_tracks — tracks per preset
}) {
  const [chips, setChips] = useState<Record<number, ChipState>>({});

  const patch = useCallback((bpm: number, p: Partial<ChipState>) => {
    setChips((c) => ({ ...c, [bpm]: { ...(c[bpm] ?? BLANK), ...p } }));
  }, []);

  // On mount: sync each chip with what's actually pinned + cached, healing the
  // index first (the browser may have evicted storage since last time).
  useEffect(() => {
    if (!offlineSupported()) return;
    let stale = false;
    void (async () => {
      await reconcileIndex();
      for (const p of presets) {
        const pinned = pinnedRunQueue<RunQueueResponse>(p.bpm);
        if (!pinned?.data?.tracks?.length) continue;
        const paths = pinned.data.tracks.map((t) => t.path);
        const present = await cachedPaths(paths);
        if (!stale) patch(p.bpm, { cached: present.size, total: paths.length });
      }
    })();
    return () => { stale = true; };
    // Keyed on the preset BPMs: re-sync if the admin re-configures the presets.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [presets.map((p) => p.bpm).join(","), patch]);

  if (!offlineSupported()) return null;

  async function begin(bpm: number) {
    patch(bpm, { phase: "busy", error: undefined });
    try {
      const resp = await api.get<RunQueueResponse>(`/api/run/queue?bpm=${bpm}&count=${count}${scope}`);
      if (!resp.tracks.length) {
        patch(bpm, { phase: "error", error: "No tracks reach this BPM" });
        return;
      }
      const est = await estimateSize(resp.tracks.map((t) => t.path));
      patch(bpm, { phase: "confirm", pending: resp, estBytes: est.bytes });
    } catch {
      patch(bpm, { phase: "error", error: "Couldn't build the queue" });
    }
  }

  async function download(bpm: number, resp: RunQueueResponse) {
    // Pin before downloading: a partial download is still a better offline
    // start than nothing (cached tracks serve locally, the rest stream).
    pinRunQueue(bpm, resp);
    const paths = resp.tracks.map((t) => t.path);
    patch(bpm, { phase: "downloading", pending: undefined, total: paths.length, cached: 0 });
    const final = await preloadMany(paths, (progress) => patch(bpm, { progress, cached: progress.ok }));
    patch(bpm, {
      phase: final.ok > 0 ? "idle" : "error",
      error: final.ok > 0 ? undefined : "Download failed",
      progress: undefined, cached: final.ok, total: paths.length,
    });
  }

  function onChip(p: { name: string; bpm: number }) {
    const st = chips[p.bpm] ?? BLANK;
    if (st.phase === "busy" || st.phase === "downloading") return;
    if (st.phase === "confirm" && st.pending) void download(p.bpm, st.pending);
    else void begin(p.bpm);   // idle / error / ready — (re)build and re-estimate
  }

  const label = (p: { name: string; bpm: number }, st: ChipState): string => {
    switch (st.phase) {
      case "busy": return `${p.name} · sizing…`;
      case "confirm": return `${p.name} · ${fmtBytes(st.estBytes ?? 0)}?`;
      case "downloading": return `${p.name} · ${st.progress?.done ?? 0}/${st.total}`;
      case "error": return `${p.name} · ${st.error}`;
      default:
        if (st.total > 0 && st.cached >= st.total) return `${p.name} ✓`;
        if (st.cached > 0) return `${p.name} · ${st.cached}/${st.total}`;
        return p.name;
    }
  };

  const title = (p: { name: string; bpm: number }, st: ChipState): string => {
    if (st.phase === "confirm") return `Tap again to download ${st.pending?.tracks.length ?? count} tracks (${fmtBytes(st.estBytes ?? 0)}) for offline runs at ${p.bpm} BPM`;
    if (st.total > 0 && st.cached >= st.total) return `Ready for offline runs at ${p.bpm} BPM — tap to prepare a fresh queue`;
    return `Prepare a ${p.bpm} BPM queue for places without network — builds a queue and downloads its tracks to this device`;
  };

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", marginBottom: 12 }}>
      <span style={{ fontSize: 11, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em", flexShrink: 0 }}>
        Offline
      </span>
      {presets.map((p) => {
        const st = chips[p.bpm] ?? BLANK;
        const ready = st.phase === "idle" && st.total > 0 && st.cached >= st.total;
        return (
          <button
            key={p.bpm}
            className="btn btn-ghost btn-sm"
            onClick={() => onChip(p)}
            disabled={st.phase === "busy" || st.phase === "downloading"}
            title={title(p, st)}
            style={{
              fontSize: 11, padding: "3px 9px", borderRadius: 999,
              color: st.phase === "error" ? "var(--err-fg)"
                : ready ? "var(--ok-fg, var(--accent-2))"
                : st.phase === "confirm" ? "var(--accent-2)" : "var(--muted)",
            }}
          >
            {label(p, st)}
          </button>
        );
      })}
    </div>
  );
}
