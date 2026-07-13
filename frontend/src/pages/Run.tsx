import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { usePlayer, lockRate } from "../lib/player";
import type { RunQueueResponse, RunTrack, SettingsMap } from "../lib/types";
import { Cover } from "../components/Artwork";
import { fmtTime, useAudioTime } from "../hooks/useAudioTime";
import { useWaveform } from "../hooks/useWaveform";
import { useTitle } from "../hooks/useTitle";

const TARGET_KEY = "bpm.run.target";
const MODE_KEY = "bpm.run.mode";

const PRESET_DEFAULTS = [
  { name: "Warmup", bpm: 120 }, { name: "Easy", bpm: 155 },
  { name: "Steady", bpm: 165 }, { name: "Tempo", bpm: 175 },
];

function clampTarget(v: number): number {
  return Math.max(30, Math.min(300, Math.round(v)));
}

/** The octave candidate (×½ / ×1 / ×2) closest to the target. */
function fold(bpm: number, target: number, octave: boolean): number {
  const cands = octave ? [bpm, bpm / 2, bpm * 2] : [bpm];
  return cands.reduce((a, b) => (Math.abs(target / b - 1) < Math.abs(target / a - 1) ? b : a));
}

function foldLabel(bpm: number, folded: number): string {
  return folded > bpm * 1.5 ? "×2" : folded < bpm * 0.75 ? "×½" : "×1";
}

/** Star toggle used in the queue list. */
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
  const { current, playing, audioRef } = player;
  const { time, dur } = useAudioTime(audioRef);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  useWaveform(canvasRef, audioRef, current?.path || "", !!current, !!current);

  const settingsQ = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<{ settings: SettingsMap }>("/api/settings"),
  });
  const cfg = settingsQ.data?.settings;
  // Presets accept both shapes: {name,bpm} dicts and the legacy number list.
  const presets = [0, 1, 2, 3].map((i) => {
    const raw = Array.isArray(cfg?.run_presets) ? (cfg!.run_presets as unknown[])[i] : undefined;
    if (raw && typeof raw === "object") {
      const p = raw as { name?: unknown; bpm?: unknown };
      return { name: String(p.name ?? PRESET_DEFAULTS[i].name), bpm: Number(p.bpm ?? PRESET_DEFAULTS[i].bpm) };
    }
    if (typeof raw === "number") return { name: PRESET_DEFAULTS[i].name, bpm: raw };
    return PRESET_DEFAULTS[i];
  });
  const octave = cfg?.run_octave_fold == null ? true : Boolean(cfg.run_octave_fold);
  const stretchLimitPct = cfg?.run_stretch_limit_pct == null ? 15 : Number(cfg.run_stretch_limit_pct);
  const queueSize = cfg?.run_queue_size == null ? 20 : Number(cfg.run_queue_size);

  const [target, setTargetState] = useState(() => {
    const saved = Number(localStorage.getItem(TARGET_KEY));
    return saved >= 30 && saved <= 300 ? saved : 155;
  });
  const [mode, setModeState] = useState<"steps" | "presets">(() =>
    localStorage.getItem(MODE_KEY) === "steps" ? "steps" : "presets");
  const [lockOn, setLockOn] = useState(true);
  const [queueInfo, setQueueInfo] = useState<RunQueueResponse | null>(null);
  const [building, setBuilding] = useState(false);
  const [buildErr, setBuildErr] = useState("");

  const liveLock = { target, octave, stretchLimitPct };
  const running = !!player.tempoLock;

  function setMode(m: "steps" | "presets") {
    setModeState(m);
    localStorage.setItem(MODE_KEY, m);
  }

  /** Change the target; a live tempo lock follows immediately (playbackRate is
   *  cheap to move), the queue itself only changes on the next Start. */
  function setTarget(v: number) {
    const t = clampTarget(v);
    setTargetState(t);
    localStorage.setItem(TARGET_KEY, String(t));
    if (running && lockOn) player.setTempoLock({ ...liveLock, target: t });
  }

  function toggleLock() {
    const on = !lockOn;
    setLockOn(on);
    player.setTempoLock(on && (running || queueInfo || current) ? liveLock : null);
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

  // The "NATIVE 78 · 0.99× ×2 → 155 BPM" line for the playing track.
  const nativeBpm = current?.bpm ?? null;
  const folded = nativeBpm ? fold(nativeBpm, target, octave) : null;
  const rate = lockOn ? lockRate(nativeBpm, liveLock) : 1;
  const shifted = folded ? Math.round(folded * rate) : null;

  const stepBtn: React.CSSProperties = {
    minWidth: 64, minHeight: 52, fontFamily: "var(--mono)", fontSize: 16, fontWeight: 600,
  };
  const ctlBtn: React.CSSProperties = {
    width: 54, height: 54, borderRadius: 999, display: "inline-flex", alignItems: "center",
    justifyContent: "center", background: "var(--surface)", border: "1px solid var(--border)",
    color: "var(--text)", cursor: "pointer",
  };

  return (
    <div style={{ maxWidth: 520, margin: "0 auto" }}>
      {/* Lock pill */}
      <div style={{ display: "flex", justifyContent: "center", marginBottom: 18 }}>
        <button
          onClick={toggleLock}
          aria-pressed={lockOn}
          title={lockOn
            ? `Tempo locked — every track stretches onto ${target} BPM (pitch preserved, max ±${stretchLimitPct}%)`
            : "Tempo unlocked — tracks play at native speed"}
          style={{
            display: "inline-flex", alignItems: "center", gap: 8, padding: "9px 20px",
            borderRadius: 999, fontFamily: "var(--mono)", fontSize: 13, fontWeight: 600,
            cursor: "pointer", letterSpacing: "0.02em",
            background: lockOn ? "var(--accent-soft)" : "var(--surface)",
            border: `1px solid ${lockOn ? "var(--accent-border)" : "var(--border)"}`,
            color: lockOn ? "var(--accent-2)" : "var(--muted)",
          }}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            {lockOn
              ? <><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></>
              : <><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 7.5-1.9" /></>}
          </svg>
          {lockOn ? "BPM Locked" : "BPM Unlocked"}
        </button>
      </div>

      {/* Now playing: cover + track details */}
      {current && (
        <div style={{ textAlign: "center", marginBottom: 20 }}>
          <div style={{ display: "flex", justifyContent: "center", marginBottom: 14 }}>
            <Cover path={current.path} size={240} style={{ borderRadius: 20, boxShadow: "0 18px 50px -18px rgba(0,0,0,0.6)" }} />
          </div>
          <div style={{ fontSize: 21, fontWeight: 600, letterSpacing: "-0.015em", marginBottom: 3 }}>{current.title}</div>
          {current.artist && <div style={{ fontSize: 14, color: "var(--muted)" }}>{current.artist}</div>}
        </div>
      )}

      {/* Target */}
      <div style={{ textAlign: "center", marginBottom: 16 }}>
        <div style={{ fontFamily: "var(--mono)", fontSize: 12, letterSpacing: "0.28em", color: "var(--muted)", marginBottom: 2 }}>
          TARGET BPM
        </div>
        <div style={{ fontFamily: "var(--mono)", fontSize: 88, fontWeight: 700, letterSpacing: "-0.05em", lineHeight: 1.05, fontVariantNumeric: "tabular-nums" }}>
          {Math.round(target)}
        </div>
        {nativeBpm != null && folded != null && shifted != null && (
          <div style={{ fontFamily: "var(--mono)", fontSize: 13, color: "var(--muted)", display: "inline-flex", alignItems: "center", gap: 8, marginTop: 4 }}>
            <span style={{ letterSpacing: "0.1em" }}>NATIVE {Math.round(nativeBpm)}</span>
            <span style={{ display: "inline-block", position: "relative", width: 8, height: 8, flexShrink: 0 }} aria-hidden>
              <span style={{ position: "absolute", inset: 0, borderRadius: 999, background: "var(--accent)", opacity: 0.5 }} />
              <span style={{ position: "absolute", inset: 0, borderRadius: 999, background: "var(--accent)", animation: playing ? `pulse-beat ${Math.round(60000 / (lockOn ? target : (folded ?? target)))}ms ease-out infinite` : "none" }} />
            </span>
            {lockOn && <span>{rate.toFixed(2)}×</span>}
            <span>{foldLabel(nativeBpm, folded)}</span>
            <span>→ <span style={{ color: "var(--accent-2)", fontWeight: 600 }}>{shifted} BPM</span></span>
          </div>
        )}
      </div>

      {/* Steps | Presets */}
      <div style={{ display: "flex", justifyContent: "center", gap: 4, marginBottom: 12 }}>
        {(["presets", "steps"] as const).map((m) => (
          <button
            key={m}
            className={"btn btn-sm " + (mode === m ? "btn-primary" : "btn-bare")}
            style={{ minWidth: 74 }}
            onClick={() => setMode(m)}
            aria-pressed={mode === m}
          >
            {m === "presets" ? "Presets" : "± Steps"}
          </button>
        ))}
      </div>
      {mode === "steps" ? (
        <div style={{ display: "flex", justifyContent: "center", gap: 10, flexWrap: "wrap", marginBottom: 20 }}>
          <button className="btn btn-ghost" style={stepBtn} onClick={() => setTarget(target - 5)} aria-label="Minus 5 BPM">−5</button>
          <button className="btn btn-ghost" style={stepBtn} onClick={() => setTarget(target - 1)} aria-label="Minus 1 BPM">−1</button>
          <button className="btn btn-ghost" style={stepBtn} onClick={() => setTarget(target + 1)} aria-label="Plus 1 BPM">+1</button>
          <button className="btn btn-ghost" style={stepBtn} onClick={() => setTarget(target + 5)} aria-label="Plus 5 BPM">+5</button>
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 20 }}>
          {presets.map((p, i) => {
            const active = target === p.bpm;
            return (
              <button
                key={i}
                onClick={() => setTarget(p.bpm)}
                aria-pressed={active}
                style={{
                  padding: "13px 10px", borderRadius: 14, cursor: "pointer", textAlign: "center",
                  background: active ? "var(--accent-soft)" : "var(--surface)",
                  border: `1px solid ${active ? "var(--accent-border)" : "var(--border)"}`,
                  color: "var(--text)",
                }}
              >
                <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 2 }}>{p.name}</div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 13, color: active ? "var(--accent-2)" : "var(--muted)" }}>{p.bpm} BPM</div>
              </button>
            );
          })}
        </div>
      )}

      {/* Build / end */}
      <div style={{ display: "flex", justifyContent: "center", gap: 10, marginBottom: 8 }}>
        <button className="btn btn-primary" style={{ minHeight: 44, padding: "0 22px" }} disabled={building} onClick={startRun}>
          {building ? "Building…" : queueInfo || running ? "Rebuild queue" : "Start run"}
        </button>
        {(running || current) && (
          <button className="btn btn-ghost" style={{ minHeight: 44 }} onClick={endRun}>End run</button>
        )}
      </div>
      {staleQueue && (
        <div style={{ textAlign: "center", fontSize: 12, color: "var(--warn-fg)", marginBottom: 6 }}>
          Queue was built for {queueInfo!.target} BPM — tracks stretch to follow {target}, hit Rebuild for a fresh match.
        </div>
      )}
      {buildErr && <div style={{ textAlign: "center", fontSize: 12, color: "var(--err-fg)", marginBottom: 6 }}>{buildErr}</div>}

      {/* Waveform + transport */}
      {current && (
        <div style={{ marginTop: 14, marginBottom: 22 }}>
          <canvas ref={canvasRef} style={{ width: "100%", height: 58, display: "block", cursor: "pointer" }} />
          <div style={{ display: "flex", justifyContent: "space-between", fontFamily: "var(--mono)", fontSize: 12, color: "var(--muted)", marginTop: 4 }}>
            <span>{fmtTime(time)}</span>
            <span>-{fmtTime(Math.max(0, dur - time))}</span>
          </div>
          <div style={{ display: "flex", justifyContent: "center", alignItems: "center", gap: 22, marginTop: 14 }}>
            <button style={ctlBtn} onClick={player.prev} aria-label="Previous" title="Previous">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M6 5v14h2V5H6zm3 7l11 7V5l-11 7z" /></svg>
            </button>
            <button
              style={{ ...ctlBtn, width: 76, height: 76, background: "var(--accent)", border: "none", boxShadow: "0 10px 34px -10px var(--accent)" }}
              onClick={player.toggle}
              aria-label={playing ? "Pause" : "Play"}
            >
              {playing ? (
                <svg width="26" height="26" viewBox="0 0 24 24" fill="white"><rect x="6" y="4" width="4" height="16" rx="1" /><rect x="14" y="4" width="4" height="16" rx="1" /></svg>
              ) : (
                <svg width="26" height="26" viewBox="0 0 24 24" fill="white" style={{ marginLeft: 3 }}><polygon points="6,4 20,12 6,20" /></svg>
              )}
            </button>
            <button style={ctlBtn} onClick={player.next} aria-label="Next" title="Next">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M16 5v14h2V5h-2zM4 19l11-7L4 5v14z" /></svg>
            </button>
          </div>
        </div>
      )}

      {/* Queue (scroll below the player) */}
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
              const tFolded = fold(t.bpm, target, octave);
              const tRate = lockOn ? lockRate(t.bpm, liveLock) : 1;
              const tShifted = Math.round(tFolded * tRate);
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
                  <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--muted)", flexShrink: 0, textAlign: "right" }} title="native BPM · octave · stretch → what you hear">
                    {Math.round(t.bpm)} {foldLabel(t.bpm, tFolded)}
                    {lockOn && <span> {tRate.toFixed(2)}×</span>}
                    <span> → <span style={{ color: "var(--accent-2)", fontWeight: 600 }}>{tShifted}</span></span>
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
