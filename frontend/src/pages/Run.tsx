import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { usePlayer, lockRate } from "../lib/player";
import type { RunQueueResponse, RunTrack, SettingsMap } from "../lib/types";
import { BpmDisplay } from "../components/BpmDisplay";
import { useTitle } from "../hooks/useTitle";

const TARGET_KEY = "bpm.run.target";

function clampTarget(v: number): number {
  return Math.max(30, Math.min(300, Math.round(v)));
}

/** Star toggle used in the queue preview list. */
function Star({ on, onToggle }: { on: boolean; onToggle: () => void }) {
  return (
    <button
      className="btn btn-bare btn-sm"
      style={{ padding: 4, color: on ? "var(--warn-fg)" : "var(--muted)", flexShrink: 0 }}
      onClick={onToggle}
      aria-pressed={on}
      aria-label={on ? "Unstar" : "Star"}
      title={on ? "Unstar" : "Star — starred tracks are preferred when building run queues"}
    >
      <svg width="15" height="15" viewBox="0 0 24 24" fill={on ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round">
        <polygon points="12,2.5 15,9 22,9.8 17,14.6 18.2,21.6 12,18.2 5.8,21.6 7,14.6 2,9.8 9,9" />
      </svg>
    </button>
  );
}

export default function Run() {
  useTitle("Run");
  const player = usePlayer();
  const settingsQ = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<{ settings: SettingsMap }>("/api/settings"),
  });
  const cfg = settingsQ.data?.settings;
  const presets: number[] = Array.isArray(cfg?.run_presets)
    ? (cfg!.run_presets as unknown[]).map(Number).slice(0, 4)
    : [140, 150, 160, 170];
  const octave = cfg?.run_octave_fold == null ? true : Boolean(cfg.run_octave_fold);
  const stretchLimitPct = cfg?.run_stretch_limit_pct == null ? 15 : Number(cfg.run_stretch_limit_pct);
  const queueSize = cfg?.run_queue_size == null ? 20 : Number(cfg.run_queue_size);

  const [target, setTargetState] = useState(() => {
    const saved = Number(localStorage.getItem(TARGET_KEY));
    return saved >= 30 && saved <= 300 ? saved : 150;
  });
  const [lockOn, setLockOn] = useState(true);
  const [queueInfo, setQueueInfo] = useState<RunQueueResponse | null>(null);
  const [building, setBuilding] = useState(false);
  const [buildErr, setBuildErr] = useState("");

  const liveLock = { target, octave, stretchLimitPct };
  const running = !!player.tempoLock;

  /** Change the target; a live tempo lock follows immediately (playbackRate is
   *  cheap to move), the queue itself only changes on the next Start. */
  function setTarget(v: number) {
    const t = clampTarget(v);
    setTargetState(t);
    localStorage.setItem(TARGET_KEY, String(t));
    if (running && lockOn) player.setTempoLock({ ...liveLock, target: t });
  }

  function toggleLock(on: boolean) {
    setLockOn(on);
    player.setTempoLock(on && (running || queueInfo) ? liveLock : null);
  }

  async function startRun() {
    setBuilding(true);
    setBuildErr("");
    try {
      const resp = await api.get<RunQueueResponse>(`/api/run/queue?bpm=${target}`);
      setQueueInfo(resp);
      if (!resp.tracks.length) {
        setBuildErr("No tracks match this BPM — widen the tolerance in Settings or pick another target.");
        return;
      }
      player.playQueue(
        resp.tracks.map((t) => ({ path: t.path, title: t.title, artist: t.artist, bpm: t.bpm })),
        0, { shuffle: false },
      );
      player.setTempoLock(lockOn ? liveLock : null);
    } catch (e) {
      setBuildErr(e instanceof Error ? e.message : "Failed to build the queue");
    } finally {
      setBuilding(false);
    }
  }

  function endRun() {
    player.setTempoLock(null);
    player.stop();
    setQueueInfo(null);
  }

  async function toggleStar(t: RunTrack) {
    const next = !t.starred;
    // Optimistic: the run has begun, a failed star is not worth interrupting it.
    setQueueInfo((qi) => qi && {
      ...qi,
      tracks: qi.tracks.map((x) => (x.path === t.path ? { ...x, starred: next } : x)),
    });
    api.post("/api/track/star", { path: t.path, starred: next }).catch(() => {});
  }

  const staleQueue = queueInfo && Math.abs(queueInfo.target - target) > 0.5;
  const adjust = (n: number) => () => setTarget(target + n);
  const bigBtn: React.CSSProperties = {
    minWidth: 58, minHeight: 48, fontFamily: "var(--mono)", fontSize: 15, fontWeight: 600,
  };

  return (
    <>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 28, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 4 }}>Run</h1>
        <p style={{ fontSize: 13, color: "var(--muted)" }}>
          Pick a cadence, queue the tracks that match it{octave ? " (half/double-time counts)" : ""}, and lock the tempo so every song lands on your step.
        </p>
      </div>

      <div className="card" style={{ marginBottom: 18, textAlign: "center", padding: "26px 16px" }}>
        <div style={{ display: "flex", justifyContent: "center", marginBottom: 6 }}>
          <BpmDisplay bpm={target} sizePx={64} pulsing={player.playing && running && lockOn} beatMs={Math.round(60000 / target)} />
        </div>
        <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 18 }}>
          steps per minute {running && lockOn ? "· locked" : ""}
        </div>

        <div style={{ display: "flex", justifyContent: "center", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
          <button className="btn btn-ghost" style={bigBtn} onClick={adjust(-5)} aria-label="Minus 5 BPM">−5</button>
          <button className="btn btn-ghost" style={bigBtn} onClick={adjust(-1)} aria-label="Minus 1 BPM">−1</button>
          <button className="btn btn-ghost" style={bigBtn} onClick={adjust(+1)} aria-label="Plus 1 BPM">+1</button>
          <button className="btn btn-ghost" style={bigBtn} onClick={adjust(+5)} aria-label="Plus 5 BPM">+5</button>
        </div>

        <div style={{ display: "flex", justifyContent: "center", gap: 8, flexWrap: "wrap", marginBottom: 18 }}>
          {presets.map((p, i) => (
            <button
              key={`${p}-${i}`}
              className={"btn btn-sm " + (target === p ? "btn-primary" : "btn-ghost")}
              style={{ minWidth: 64, minHeight: 40, fontFamily: "var(--mono)", fontWeight: 600 }}
              onClick={() => setTarget(p)}
            >
              {p}
            </button>
          ))}
        </div>

        <div style={{ display: "flex", justifyContent: "center", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
          <button className="btn btn-primary" style={{ minHeight: 46, padding: "0 22px" }} disabled={building} onClick={startRun}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" style={{ marginRight: 6 }}><polygon points="6,4 20,12 6,20" /></svg>
            {building ? "Building…" : queueInfo ? "Rebuild queue" : "Start run"}
          </button>
          {running && (
            <button className="btn btn-ghost" style={{ minHeight: 46 }} onClick={endRun}>End run</button>
          )}
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: lockOn ? "var(--accent-2)" : "var(--muted)", cursor: "pointer" }}
                 title={`Stretch every track onto ${target} BPM (pitch preserved, max ±${stretchLimitPct}%). Off = queue by BPM but play at native speed.`}>
            <input type="checkbox" checked={lockOn} onChange={(e) => toggleLock(e.target.checked)} />
            tempo lock
          </label>
        </div>

        {staleQueue && (
          <div style={{ marginTop: 12, fontSize: 12, color: "var(--warn-fg)" }}>
            Queue was built for {queueInfo!.target} BPM — tracks stretch to follow {target}, hit Rebuild for a fresh match.
          </div>
        )}
        {buildErr && <div style={{ marginTop: 12, fontSize: 12, color: "var(--err-fg)" }}>{buildErr}</div>}
      </div>

      {queueInfo && queueInfo.tracks.length > 0 && (
        <div className="card" style={{ padding: 0 }}>
          <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontWeight: 600, fontSize: 14 }}>Run queue · {queueInfo.tracks.length}</span>
            <span style={{ fontSize: 12, color: "var(--muted)" }}>
              built for {queueInfo.target} BPM · ±{queueInfo.tolerance_pct.toFixed(1)}%
              {queueInfo.tracks.length < queueSize ? ` · only ${queueInfo.tracks.length} of ${queueSize} matched` : ""}
            </span>
            <Link to="/settings#sec-run" style={{ marginLeft: "auto", fontSize: 12, color: "var(--accent-2)" }}>Run settings</Link>
          </div>
          <div>
            {queueInfo.tracks.map((t, i) => {
              const rate = lockOn ? lockRate(t.bpm, liveLock) : 1;
              const stretch = (rate - 1) * 100;
              const isCurrent = player.isCurrent(t.path);
              return (
                <div key={t.path} style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 16px", borderBottom: "1px solid var(--border)", background: isCurrent ? "var(--row-hover)" : "transparent" }}>
                  <Star on={t.starred} onToggle={() => toggleStar(t)} />
                  <button
                    style={{ flex: 1, minWidth: 0, textAlign: "left", background: "none", border: "none", cursor: "pointer", color: "inherit", padding: 0 }}
                    onClick={() => player.jumpTo(i)}
                    title={`Play ${t.title}`}
                  >
                    <span style={{ display: "block", fontSize: 13, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {isCurrent && <span style={{ color: "var(--accent-2)", marginRight: 6 }}>▶</span>}
                      {t.title}
                    </span>
                    {t.artist && <span style={{ display: "block", fontSize: 11, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", marginTop: 2 }}>{t.artist}</span>}
                  </button>
                  <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--muted)", flexShrink: 0, textAlign: "right" }}>
                    {t.bpm.toFixed(1)}
                    {t.run_bpm !== t.bpm && <span title="Octave-folded: you step on every beat at half/double time"> ⤳ {t.run_bpm.toFixed(0)}</span>}
                    {lockOn && Math.abs(stretch) >= 0.05 && (
                      <span style={{ color: Math.abs(stretch) > 10 ? "var(--warn-fg)" : "var(--accent-2)", marginLeft: 6 }}>
                        {stretch > 0 ? "+" : ""}{stretch.toFixed(1)}%
                      </span>
                    )}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </>
  );
}
