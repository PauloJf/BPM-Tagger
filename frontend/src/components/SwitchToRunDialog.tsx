import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { lockFits, usePlayer, type TempoLock } from "../lib/player";
import type { RunQueueResponse, SettingsMap } from "../lib/types";
import { readyCount, useRunReadiness } from "../hooks/useRunReadiness";
import {
  TARGET_KEY, clampTarget, parseRunPresets, rememberedRunSource, rememberedTarget, runOctave, runStretchLimitPct,
} from "../lib/runSettings";

const CHOICE_KEY = "bpm.run.switchChoice";
export type SwitchChoice = "build" | "stretch";

function rememberedChoice(): SwitchChoice {
  try { return localStorage.getItem(CHOICE_KEY) === "stretch" ? "stretch" : "build"; } catch { return "build"; }
}

/** Going from Listen to Run with a queue already playing: the one moment where a
 *  queue built without a pace may not fit one. Asks what to do with it.
 *
 *  - **Build** keeps the song that is playing, replaces the rest with tracks that
 *    can reach the pace (starred first), and locks.
 *  - **Stretch** keeps the whole queue and locks it; tracks that can't reach the
 *    pace play stretched as far as the limit allows and show Run's ⚠ marker.
 *
 *  It always appears, with the last answer pre-selected, so repeating a choice is
 *  one tap on Start. Picking a preset here changes the count live. */
export default function SwitchToRunDialog({ onCancel, onStarted }: { onCancel: () => void; onStarted: () => void }) {
  const player = usePlayer();
  const [target, setTarget] = useState(rememberedTarget);
  const [choice, setChoice] = useState<SwitchChoice>(rememberedChoice);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const settingsQ = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<{ settings: SettingsMap }>("/api/settings"),
  });
  const cfg = settingsQ.data?.settings;
  const presets = parseRunPresets(cfg);
  const octave = runOctave(cfg);
  const stretchLimitPct = runStretchLimitPct(cfg);
  const readinessQ = useRunReadiness();
  const { source, scope } = rememberedRunSource();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !busy) onCancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel, busy]);

  const lock: TempoLock = { target, octave, stretchLimitPct };
  const queued = player.orderedQueue;
  const fitting = queued.filter((t) => lockFits(t.bpm, lock)).length;
  const offPace = queued.length - fitting;
  // Readiness is counted per preset, so only a preset target has a number.
  const isPreset = presets.some((p) => p.bpm === target);
  const ready = isPreset ? readyCount(readinessQ.data, target, typeof source === "number" ? source : null) : null;

  async function start() {
    setBusy(true);
    setErr("");
    try { localStorage.setItem(CHOICE_KEY, choice); localStorage.setItem(TARGET_KEY, String(clampTarget(target))); } catch { /* no storage */ }
    try {
      if (choice === "stretch") {
        // Same queue, now locked; the run's refill stays inside the source the
        // queue came from (the whole library when it came from nowhere in particular).
        const ls = player.listenSource;
        player.setRunSource(ls == null ? source : ls === "library" ? null : ls);
        player.setTempoLock(lock);
      } else {
        const resp = await api.get<RunQueueResponse>(`/api/run/queue?bpm=${target}${scope}`);
        const now = player.current;
        const rest = resp.tracks.filter((t) => t.path !== now?.path);
        if (!rest.length) {
          setErr(`No other tracks can reach ${target} BPM — raise Max stretch in Settings, pick another pace, or keep this queue.`);
          return;
        }
        const built = rest.map((t) => ({ path: t.path, title: t.title, artist: t.artist, bpm: t.bpm,
          starred: t.starred, rating: t.rating, fromPlaylist: t.from_playlist, loudnessLufs: t.loudness_lufs }));
        // The song that is playing stays first, so it carries on. ⚠ playQueue()
        // exits run mode, so the source and lock are set after it (see Run's
        // launchQueue: the order is load-bearing).
        player.playQueue(now ? [now, ...built] : built, 0, { shuffle: false });
        player.setRunSource(source);
        player.setTempoLock(lock);
      }
      onStarted();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't build the queue");
    } finally {
      setBusy(false);
    }
  }

  const option = (value: SwitchChoice, title: string, detail: React.ReactNode, recommended?: boolean) => {
    const on = choice === value;
    return (
      <label
        key={value}
        style={{
          display: "flex", gap: 10, alignItems: "flex-start", padding: "10px 12px", cursor: "pointer",
          border: "1px solid " + (on ? "var(--accent)" : "var(--border)"), borderRadius: 10,
          background: on ? "var(--accent-soft, transparent)" : "transparent",
        }}
      >
        <input type="radio" name="switch-choice" checked={on} onChange={() => setChoice(value)} style={{ marginTop: 3 }} />
        <span style={{ minWidth: 0 }}>
          <span style={{ display: "block", fontWeight: 600, fontSize: 14 }}>
            {title}{recommended && <span style={{ color: "var(--muted)", fontWeight: 400, fontSize: 12 }}> · recommended</span>}
          </span>
          <span style={{ display: "block", fontSize: 12.5, color: "var(--muted)", marginTop: 2 }}>{detail}</span>
        </span>
      </label>
    );
  };

  return createPortal(
    <div
      role="dialog" aria-modal="true" aria-label="Start a run" data-testid="switch-to-run"
      onClick={() => { if (!busy) onCancel(); }}
      style={{ position: "fixed", inset: 0, zIndex: 200, background: "rgba(0,0,0,0.55)", display: "grid", placeItems: "center", padding: 16 }}
    >
      <div className="card" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 460, width: "100%", maxHeight: "90dvh", overflowY: "auto" }}>
        <div style={{ fontWeight: 600, fontSize: 16 }}>Run to a pace?</div>
        <div style={{ fontSize: 13, color: "var(--muted)", marginTop: 4 }}>
          Your queue was built without a pace, so some of it may not fit one.
        </div>

        <div role="group" aria-label="Pace" style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 14 }}>
          {!isPreset && (
            <button type="button" className="btn btn-sm btn-primary" aria-pressed>{target} BPM</button>
          )}
          {presets.map((p) => (
            <button
              key={p.name}
              type="button"
              className={"btn btn-sm" + (p.bpm === target ? " btn-primary" : "")}
              aria-pressed={p.bpm === target}
              onClick={() => setTarget(p.bpm)}
            >
              {p.name} · {p.bpm}
            </button>
          ))}
        </div>

        <div style={{ display: "grid", gap: 8, marginTop: 12 }}>
          {option("build", "Build a queue for this pace",
            <>
              The current song carries on; the rest is replaced with tracks that fit, starred first.
              {ready != null && <> <b data-testid="ready-count">{ready}</b> ready{typeof source === "number" ? " in this playlist" : source === "mine" ? " in your playlists" : " in your library"}.</>}
            </>, true)}
          {option("stretch", "Keep this queue and stretch it",
            <span data-testid="stretch-count">
              {offPace === 0
                ? <>All {queued.length} tracks can reach {target} BPM.</>
                : <><b>{fitting}</b> of {queued.length} tracks can reach {target} BPM. The other {offPace} play stretched as far as ±{stretchLimitPct}% allows, marked ⚠.</>}
            </span>)}
        </div>

        {err && <div role="alert" style={{ marginTop: 10, fontSize: 12.5, color: "var(--warn-fg)" }}>{err}</div>}

        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 16 }}>
          <button type="button" className="btn" onClick={onCancel} disabled={busy}>Cancel</button>
          <button type="button" className="btn btn-primary" onClick={start} disabled={busy}>
            {busy ? "Building…" : "Start"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
