import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { usePlayer, lockRate } from "../lib/player";
import type { RunQueueResponse, SettingsMap, TrackDetailResponse } from "../lib/types";
import { useTapTempo } from "../hooks/useTapTempo";
import { Cover } from "../components/Artwork";
import { LyricsPanel } from "../components/LyricsPanel";
import QueueSimilar from "../components/QueueSimilar";
import { fmtTime, useAudioTime } from "../hooks/useAudioTime";
import { useWaveform } from "../hooks/useWaveform";
import { useTitle } from "../hooks/useTitle";
import { useCoverGlow } from "../hooks/useCoverGlow";
import { useIsMobile } from "../hooks/useIsMobile";
import PageHeader from "../components/PageHeader";

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

/** Dislike toggle used in the queue list — excludes the track from future
 *  run-queue builds (it stays in the queue it's already in). */
function Dislike({ on, onToggle }: { on: boolean; onToggle: () => void }) {
  return (
    <button
      className="btn btn-bare btn-sm"
      style={{ padding: 4, color: on ? "var(--err-fg)" : "var(--muted)", flexShrink: 0 }}
      onClick={onToggle}
      aria-pressed={on}
      aria-label={on ? "Remove dislike" : "Dislike"}
      title={on ? "Remove dislike — eligible for run queues again" : "Dislike — never picked for a run again"}
    >
      <svg width="15" height="15" viewBox="0 0 24 24" fill={on ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17" />
      </svg>
    </button>
  );
}

/** One label/value line in the desktop track-info column. */
function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "baseline" }}>
      <span style={{ fontFamily: "var(--mono)", fontSize: 10, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--muted)", flexShrink: 0 }}>{label}</span>
      <span style={{ color: "var(--text)", textAlign: "right", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: 0 }}>{value}</span>
    </div>
  );
}

/** Tap the beat to set + lock the *current track's* native BPM. Only usable at
 *  the track's true speed, so it's disabled while the tempo lock is stretching
 *  playback — you'd otherwise be tapping the shifted tempo, not the real one. */
function TapTempoControl({ locked, nativeBpm, onSave }: {
  locked: boolean;
  nativeBpm: number | null;
  onSave: (bpm: number) => Promise<void>;
}) {
  const tap = useTapTempo(!locked);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function save() {
    if (!tap.canApply) return;
    const bpm = parseFloat(tap.display);
    setSaving(true);
    setMsg(null);
    try {
      await onSave(bpm);
      setMsg({ ok: true, text: `Saved & locked at ${bpm.toFixed(1)} BPM` });
      tap.reset();
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : "Save failed" });
    } finally {
      setSaving(false);
    }
  }

  const boxStyle: React.CSSProperties = {
    border: "1px solid var(--border)", borderRadius: 12,
    padding: "11px 13px", background: "var(--surface)",
  };
  const capLabel: React.CSSProperties = {
    fontFamily: "var(--mono)", fontSize: 10, letterSpacing: "0.1em",
    textTransform: "uppercase", color: "var(--muted)",
  };

  if (locked) {
    return (
      <div style={{ ...boxStyle, textAlign: "center" }}>
        <div style={{ ...capLabel, marginBottom: 5 }}>Tap tempo</div>
        <div style={{ fontSize: 11.5, color: "var(--warn-fg)", lineHeight: 1.45 }}>
          Tempo lock is on — playback is stretched, not at the track's true
          speed. Tap the 🔒 lock above to release it, then tap along to set and
          lock the real BPM.
        </div>
      </div>
    );
  }

  return (
    <div style={boxStyle}>
      <div style={{ display: "flex", justifyContent: "center" }}>
        <button
          className="btn btn-ghost"
          style={{ width: "100%", maxWidth: 220, minHeight: 60, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 2 }}
          onClick={tap.onTap}
          aria-label="Tap to the beat"
        >
          <span style={{ fontFamily: "var(--mono)", fontSize: 22, fontWeight: 600, letterSpacing: "0.08em", lineHeight: 1 }}>TAP</span>
          <span style={{ fontSize: 10, opacity: 0.75, letterSpacing: "0.1em" }}>OR PRESS SPACE</span>
        </button>
      </div>
      <div style={{ textAlign: "center", marginTop: 8 }}>
        <span style={{ fontFamily: "var(--mono)", fontSize: 28, fontWeight: 600, color: tap.canApply ? "var(--accent-2)" : "var(--muted)", letterSpacing: "-0.02em", fontVariantNumeric: "tabular-nums" }}>
          {tap.display}
        </span>
        <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--muted)", marginLeft: 8 }}>
          BPM{tap.taps > 0 ? ` · ${tap.taps} tap${tap.taps === 1 ? "" : "s"}` : ""}
        </span>
        {nativeBpm != null && (
          <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>current: {Math.round(nativeBpm)} BPM</div>
        )}
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 9 }}>
        <button className="btn btn-ghost btn-sm" style={{ flex: 1 }} onClick={tap.reset} disabled={tap.taps === 0 && !tap.canApply}>
          Reset
        </button>
        <button className="btn btn-primary btn-sm" style={{ flex: 1 }} onClick={save} disabled={!tap.canApply || saving}>
          {saving ? "Saving…" : "Save & lock"}
        </button>
      </div>
      {msg && (
        <div style={{ marginTop: 9, fontSize: 12, textAlign: "center", color: msg.ok ? "var(--ok-fg)" : "var(--err-fg)" }}>
          {msg.text}
        </div>
      )}
      <div style={{ marginTop: 7, fontSize: 10, color: "var(--muted)", textAlign: "center" }}>Resets after 3s of silence</div>
    </div>
  );
}

export default function Run() {
  useTitle("Run");
  const player = usePlayer();
  const { current, playing, audioRef } = player;
  const { time, dur } = useAudioTime(audioRef);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  useWaveform(canvasRef, audioRef, current?.path || "", !!current, !!current);
  const glow = useCoverGlow(current?.path ?? null);
  // Desktop gets a two-column layout: the player on the left, the run queue
  // living permanently in a full-height panel on the right (no Queue tab, no
  // hiding the cover). Below this width we keep the single-column phone layout.
  const desktop = !useIsMobile(900);

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
  const [mode, setModeState] = useState<"steps" | "presets" | "queue" | "tap">(() => {
    const m = localStorage.getItem(MODE_KEY);
    return m === "steps" || m === "queue" || m === "tap" ? m : "presets";
  });
  const [lockOn, setLockOn] = useState(true);
  const [lyricsOpen, setLyricsOpen] = useState(false);
  const [similarOpen, setSimilarOpen] = useState(false);
  const [queueInfo, setQueueInfo] = useState<RunQueueResponse | null>(null);
  const [building, setBuilding] = useState(false);
  const [buildErr, setBuildErr] = useState("");
  // Disliked tracks are excluded server-side from future queue builds, so they
  // never appear in a fresh queueInfo — this only tracks what got disliked
  // *this session* on a track that's already sitting in the current queue.
  const [dislikedPaths, setDislikedPaths] = useState<Set<string>>(() => new Set());

  // Keep the playing track fresh: fixing its BPM on the track page (or via
  // tap-tempo here) and coming back re-fetches this, and updateTrackBpm
  // re-stretches a live tempo lock. The full detail also feeds the desktop
  // track-info column (album, detector, confidence, length, plays).
  const qc = useQueryClient();
  const trackKey = ["track-bpm", current?.path || ""];
  const trackQ = useQuery({
    queryKey: trackKey,
    queryFn: () => api.get<TrackDetailResponse>(
      `/api/track?path=${encodeURIComponent(current!.path)}`),
    enabled: !!current,
    staleTime: 60_000,
  });
  const detail = trackQ.data?.track;
  const freshBpm = detail?.bpm;
  useEffect(() => {
    if (current && freshBpm != null && freshBpm !== current.bpm) {
      player.updateTrackBpm(current.path, freshBpm);
    }
  }, [current, freshBpm, player]);

  // Save + lock a tapped BPM in one call, then reflect it locally: the queue
  // track re-stretches immediately and the cached detail updates so the
  // freshBpm effect above doesn't fight the change back to the old value.
  async function saveTappedBpm(bpm: number) {
    if (!current) throw new Error("No track playing");
    const path = current.path;
    const resp = await api.post<{ ok: boolean; error?: string }>(
      "/api/save_bpm", { file_path: path, bpm });
    if (!resp.ok) throw new Error(resp.error || "Save failed");
    player.updateTrackBpm(path, bpm);
    qc.setQueryData<TrackDetailResponse>(["track-bpm", path], (old) =>
      old ? { ...old, track: { ...old.track, bpm, locked: 1 } } : old);
  }

  const liveLock = { target, octave, stretchLimitPct };
  const running = !!player.tempoLock;

  // The run queue's current track. While a Similar preview clip ducks the queue,
  // `player.current` swaps to that ephemeral clip (whose artist is the previewed
  // track's, not the run track's) — but the queue position never moves, so this
  // stays pinned to the actual run track. Anchoring the Similar panel here keeps
  // it from reloading for the clip's artist mid-preview (which unmounted the row
  // whose Preview button was just pressed, losing its play/pause state).
  const queueTrack = player.orderedQueue[player.orderPos] ?? current;

  function setMode(m: "steps" | "presets" | "queue" | "tap") {
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

  async function toggleStar(path: string, starred: boolean) {
    const next = !starred;
    // Optimistic: the run has begun, a failed star is not worth interrupting it.
    setQueueInfo((qi) => qi && {
      ...qi,
      tracks: qi.tracks.map((x) => (x.path === path ? { ...x, starred: next } : x)),
    });
    api.post("/api/track/star", { path, starred: next }).catch(() => {});
  }

  function toggleDislike(path: string, disliked: boolean) {
    const next = !disliked;
    setDislikedPaths((s) => {
      const n = new Set(s);
      if (next) n.add(path); else n.delete(path);
      return n;
    });
    api.post("/api/track/dislike", { path, disliked: next }).catch(() => {});
    // Disliking the track that's currently playing skips it right away —
    // no reason to keep listening to something you just ruled out.
    if (next && current?.path === path) player.next();
  }

  const staleQueue = queueInfo && Math.abs(queueInfo.target - target) > 0.5;

  // Star state is only known for queues built this session; a queue restored
  // after a reload renders without the star column.
  const starredByPath = new Map((queueInfo?.tracks ?? []).map((t) => [t.path, t.starred]));
  const currentDisliked = !!current && dislikedPaths.has(current.path);

  // The "NATIVE 78 · 0.99× ×2 → 155 BPM" line for the playing track.
  const nativeBpm = current?.bpm ?? null;
  const folded = nativeBpm ? fold(nativeBpm, target, octave) : null;
  const rate = lockOn ? lockRate(nativeBpm, liveLock) : 1;
  const shifted = folded ? Math.round(folded * rate) : null;

  const stepBtn: React.CSSProperties = {
    minWidth: 60, minHeight: 40, fontFamily: "var(--mono)", fontSize: 15, fontWeight: 600,
  };
  const ctlBtn: React.CSSProperties = {
    width: 54, height: 54, borderRadius: 999, display: "inline-flex", alignItems: "center",
    justifyContent: "center", background: "var(--surface)", border: "1px solid var(--border)",
    color: "var(--text)", cursor: "pointer",
  };

  // Mobile squeezes the cover so everything down to the transport fits one
  // phone screen (whatever height is left after the fixed-size sections). On
  // desktop the player column is vertically centered with room to spare, so
  // the cover can breathe. Both bump the mobile reserve for the shared header.
  const coverSize = desktop
    ? "clamp(180px, 22vh, 300px)"
    : "clamp(64px, calc(100dvh - 660px), 240px)";

  // Ambient glow tinted from the cover art, sitting behind the nav/content
  // (z-index -1 paints above the plain body background, below everything with
  // default stacking). Crossfades between tracks via useCoverGlow. Portaled
  // straight to <body> — `.container` briefly carries a transform during its
  // page-enter animation, and any transformed ancestor becomes the containing
  // block for a `position: fixed` descendant, which would misplace this
  // relative to `.container` instead of the viewport.
  const glowLayer = createPortal(
    <div
      aria-hidden
      style={{
        position: "fixed", inset: 0, zIndex: -1, pointerEvents: "none",
        transition: "opacity 0.45s ease",
        background: glow.background, opacity: glow.opacity,
      }}
    />,
    document.body,
  );

  // Uniform page header — mono eyebrow + title on the left, the primary action
  // (Start / Rebuild) and End pinned top-right, matching the header + primary
  // action placement every other page uses. Shared by both layouts, so the
  // build/end controls no longer float mid-column (mobile) or sit buried in the
  // controls column (desktop).
  const pageHeader = (
    <PageHeader
      title="Run"
      subtitle="Tempo-matched player — lock every track onto your cadence."
      actions={<>
        <button className="btn btn-primary" style={{ minHeight: 40, whiteSpace: "nowrap" }} disabled={building} onClick={startRun}>
          {building ? "Building…" : queueInfo || running ? "Rebuild" : "Start run"}
        </button>
        {(running || current) && (
          <button className="btn btn-ghost" style={{ minHeight: 40 }} onClick={endRun}>End</button>
        )}
      </>}
    />
  );

  // Now playing: cover + track details.
  const nowPlaying = current && (
    <div style={{ textAlign: "center", marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "center", marginBottom: 10 }}>
        <Cover path={current.path} size={240} style={{ width: coverSize, height: coverSize, objectFit: "cover", borderRadius: 20, boxShadow: "0 18px 50px -18px rgba(0,0,0,0.6)" }} />
      </div>
      <Link
        to={`/track?path=${encodeURIComponent(current.path)}`}
        title="Open the track page — review or fix its BPM"
        style={{
          display: "block", fontSize: 18, fontWeight: 600, letterSpacing: "-0.015em", marginBottom: 2,
          color: "inherit", textDecoration: "none",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}
      >
        {current.title}
      </Link>
      {current.artist && <div style={{ fontSize: 13, color: "var(--muted)" }}>{current.artist}</div>}
    </div>
  );

  // Target BPM readout + lock toggle + the NATIVE→BPM line for the playing track.
  const targetBlock = (
    <div style={{ textAlign: "center", marginBottom: 14 }}>
      <div style={{ fontFamily: "var(--mono)", fontSize: 12, letterSpacing: "0.28em", color: "var(--muted)", marginBottom: 2 }}>
        TARGET BPM
      </div>
      <div style={{ position: "relative", display: "inline-block" }}>
        <div style={{ fontFamily: "var(--mono)", fontSize: "min(88px, 12dvh)", fontWeight: 700, letterSpacing: "-0.05em", lineHeight: 1.05, fontVariantNumeric: "tabular-nums" }}>
          {Math.round(target)}
        </div>
        <button
          onClick={toggleLock}
          aria-pressed={lockOn}
          aria-label={lockOn ? "BPM locked" : "BPM unlocked"}
          title={lockOn
            ? `Tempo locked — every track stretches onto ${target} BPM (pitch preserved, max ±${stretchLimitPct}%)`
            : "Tempo unlocked — tracks play at native speed"}
          style={{
            position: "absolute", left: "100%", top: "50%", transform: "translateY(-50%)", marginLeft: 12,
            width: 38, height: 38, borderRadius: 999, display: "inline-flex", alignItems: "center",
            justifyContent: "center", cursor: "pointer",
            background: lockOn ? "var(--accent-soft)" : "var(--surface)",
            border: `1px solid ${lockOn ? "var(--accent-border)" : "var(--border)"}`,
            color: lockOn ? "var(--accent-2)" : "var(--muted)",
          }}
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            {lockOn
              ? <><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></>
              : <><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 7.5-1.9" /></>}
          </svg>
        </button>
      </div>
      {nativeBpm != null && folded != null && shifted != null && (
        <div style={{ display: "flex", justifyContent: "center", marginTop: 10 }}>
          <div
            style={{
              display: "inline-flex", alignItems: "center", gap: 11,
              fontFamily: "var(--mono)", fontSize: 12,
              padding: "6px 6px 6px 14px", borderRadius: 999,
              background: "var(--surface)", border: "1px solid var(--border)",
            }}
            title="Track's native BPM → octave fold → tempo stretch → the cadence you hear"
          >
            <span style={{ color: "var(--muted)", letterSpacing: "0.08em" }}>NATIVE {Math.round(nativeBpm)}</span>
            <span style={{ display: "inline-block", position: "relative", width: 8, height: 8, flexShrink: 0 }} aria-hidden>
              <span style={{ position: "absolute", inset: 0, borderRadius: 999, background: "var(--accent)", opacity: 0.5 }} />
              {/* Locked: pulse to the target cadence (what your feet follow).
                  Unlocked: pulse to the track's true native BPM — it's playing
                  at native speed, so the octave-folded value would drift out
                  of sync with what you actually hear. */}
              <span style={{ position: "absolute", inset: 0, borderRadius: 999, background: "var(--accent)", animation: playing ? `pulse-beat ${Math.round(60000 / (lockOn ? target : nativeBpm))}ms ease-out infinite` : "none" }} />
            </span>
            {lockOn && <span style={{ color: "var(--muted)" }}>{rate.toFixed(2)}×</span>}
            <span style={{ color: "var(--muted)" }}>{foldLabel(nativeBpm, folded)}</span>
            <span style={{ color: "var(--muted)", opacity: 0.6 }}>→</span>
            {/* The result — the cadence your feet actually follow — carried in a
                filled accent pill so it reads as the payoff, not just more mono text. */}
            <span style={{ background: "var(--accent-soft)", color: "var(--accent-2)", fontWeight: 600, borderRadius: 999, padding: "3px 10px" }}>{shifted} BPM</span>
          </div>
        </div>
      )}
    </div>
  );

  // Steps (± buttons) and Presets grid — the two target-picking controls that
  // appear in both layouts (mobile also folds the queue in here as a tab).
  const stepsRow = (
    <div style={{ display: "flex", justifyContent: "center", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
      <button className="btn btn-ghost" style={stepBtn} onClick={() => setTarget(target - 5)} aria-label="Minus 5 BPM">−5</button>
      <button className="btn btn-ghost" style={stepBtn} onClick={() => setTarget(target - 1)} aria-label="Minus 1 BPM">−1</button>
      <button className="btn btn-ghost" style={stepBtn} onClick={() => setTarget(target + 1)} aria-label="Plus 1 BPM">+1</button>
      <button className="btn btn-ghost" style={stepBtn} onClick={() => setTarget(target + 5)} aria-label="Plus 5 BPM">+5</button>
    </div>
  );

  const presetsGrid = (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 12 }}>
      {presets.map((p, i) => {
        const active = target === p.bpm;
        return (
          <button
            key={i}
            onClick={() => setTarget(p.bpm)}
            aria-pressed={active}
            style={{
              padding: "9px 14px", borderRadius: 12, cursor: "pointer",
              display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8,
              background: active ? "var(--accent-soft)" : "var(--surface)",
              border: `1px solid ${active ? "var(--accent-border)" : "var(--border)"}`,
              color: "var(--text)",
            }}
          >
            <span style={{ fontSize: 14, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.name}</span>
            <span style={{ fontFamily: "var(--mono)", fontSize: 12, flexShrink: 0, color: active ? "var(--accent-2)" : "var(--muted)" }}>{p.bpm}</span>
          </button>
        );
      })}
    </div>
  );

  // Status messages beneath the controls (stale queue / build + stream errors).
  // The build/end buttons themselves now live in the shared page header.
  const buildMessages = (
    <>
      {staleQueue && (
        <div style={{ textAlign: "center", fontSize: 12, color: "var(--warn-fg)", marginBottom: 6 }}>
          Queue was built for {queueInfo!.target} BPM — tracks stretch to follow {target}, hit Rebuild for a fresh match.
        </div>
      )}
      {buildErr && <div style={{ textAlign: "center", fontSize: 12, color: "var(--err-fg)", marginBottom: 6 }}>{buildErr}</div>}
      {/* With the global player bar hidden on this page, stream errors would
          otherwise be invisible here. */}
      {player.error && <div style={{ textAlign: "center", fontSize: 12, color: "var(--err-fg)", marginBottom: 6 }}>{player.error}</div>}
    </>
  );

  // Desktop-only: extra track facts for the info column, drawn from the full
  // detail fetch (album, per-track BPM confidence, length, play count).
  const trackDetails = detail && (
    <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 6, fontSize: 12, textAlign: "left" }}>
      {detail.album && (
        <DetailRow label="Album" value={detail.album + (detail.year ? ` · ${detail.year}` : "")} />
      )}
      <DetailRow
        label="Native BPM"
        value={detail.bpm != null ? `${detail.bpm.toFixed(1)}${detail.locked ? " · locked" : ""}` : "—"}
      />
      <DetailRow
        label="Detector"
        value={(detail.detector || "—") + (detail.bpm_confidence != null ? ` · ${Math.round(detail.bpm_confidence * 100)}% conf` : "")}
      />
      <DetailRow
        label="Length"
        value={
          (detail.duration_ms ? fmtTime(detail.duration_ms / 1000) : "—") +
          (detail.play_count != null ? ` · ${detail.play_count} play${detail.play_count === 1 ? "" : "s"}` : "")
        }
      />
    </div>
  );

  // Waveform + transport controls (dislike / prev / play / next / lyrics).
  const transport = current && (
    <div className="run-transport" style={{ marginTop: desktop ? 20 : 10, marginBottom: 16 }}>
      <canvas ref={canvasRef} style={{ width: "100%", height: 44, display: "block", cursor: "pointer" }} />
      <div style={{ display: "flex", justifyContent: "space-between", fontFamily: "var(--mono)", fontSize: 12, color: "var(--muted)", marginTop: 4 }}>
        <span>{fmtTime(time)}</span>
        <span>-{fmtTime(Math.max(0, dur - time))}</span>
      </div>
      <div style={{ position: "relative", display: "flex", justifyContent: "center", alignItems: "center", gap: 22, marginTop: 12 }}>
        <button
          style={{
            ...ctlBtn, width: 40, height: 40, position: "absolute", left: 0,
            color: currentDisliked ? "var(--err-fg)" : "var(--muted)",
            borderColor: currentDisliked ? "var(--err-fg)" : "var(--border)",
          }}
          onClick={() => current && toggleDislike(current.path, currentDisliked)}
          aria-label={currentDisliked ? "Remove dislike" : "Dislike"}
          aria-pressed={currentDisliked}
          title={currentDisliked ? "Remove dislike — eligible for run queues again" : "Dislike — never picked for a run again"}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill={currentDisliked ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17" />
          </svg>
        </button>
        <button style={ctlBtn} onClick={player.prev} aria-label="Previous" title="Previous">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M6 5v14h2V5H6zm3 7l11 7V5l-11 7z" /></svg>
        </button>
        <button
          style={{ ...ctlBtn, width: desktop ? 78 : 68, height: desktop ? 78 : 68, background: "var(--accent)", border: "none", boxShadow: "0 12px 40px -10px var(--accent)" }}
          onClick={player.toggle}
          aria-label={playing ? "Pause" : "Play"}
        >
          {playing ? (
            <svg width={desktop ? 30 : 26} height={desktop ? 30 : 26} viewBox="0 0 24 24" fill="white"><rect x="6" y="4" width="4" height="16" rx="1" /><rect x="14" y="4" width="4" height="16" rx="1" /></svg>
          ) : (
            <svg width={desktop ? 30 : 26} height={desktop ? 30 : 26} viewBox="0 0 24 24" fill="white" style={{ marginLeft: 3 }}><polygon points="6,4 20,12 6,20" /></svg>
          )}
        </button>
        <button style={ctlBtn} onClick={player.next} aria-label="Next" title="Next">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M16 5v14h2V5h-2zM4 19l11-7L4 5v14z" /></svg>
        </button>
        <button
          style={{
            ...ctlBtn, width: 40, height: 40, position: "absolute", right: 0,
            color: lyricsOpen ? "var(--accent-2)" : "var(--muted)",
            borderColor: lyricsOpen ? "var(--accent-border)" : "var(--border)",
          }}
          onClick={() => setLyricsOpen((o) => !o)}
          aria-label="Show lyrics"
          aria-expanded={lyricsOpen}
          title="Lyrics"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
            <path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v3" />
          </svg>
        </button>
      </div>
    </div>
  );

  // Lyrics drawer: anchored to a zero-height strip at the bottom of the
  // viewport, since the run page has no player bar to hang it off.
  const lyricsDrawer = lyricsOpen && current && (
    <div style={{ position: "fixed", left: 0, right: 0, bottom: 0, zIndex: 61 }}>
      <LyricsPanel path={current.path} audioRef={audioRef} onClose={() => setLyricsOpen(false)} />
    </div>
  );

  // The run queue panel. `fill` makes it stretch to its container's full height
  // (the desktop side column); otherwise it sizes to its content with a
  // viewport-relative max height (the mobile Queue tab).
  const renderQueuePanel = (fill: boolean) => {
    const cardStyle: React.CSSProperties = fill
      ? { padding: 0, margin: 0, height: "100%", display: "flex", flexDirection: "column" }
      : { padding: 0, marginBottom: 12 };
    const listStyle: React.CSSProperties = fill
      ? { flex: 1, minHeight: 0, overflowY: "auto" }
      : { maxHeight: "clamp(140px, calc(100dvh - 500px), 420px)", overflowY: "auto" };
    return (
      <div className="card" style={cardStyle}>
        <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap", flexShrink: 0 }}>
          <span style={{ fontWeight: 600, fontSize: 13 }}>Run queue · {player.orderedQueue.length}</span>
          {queueInfo && (
            <span style={{ fontSize: 11, color: "var(--muted)" }}>
              built for {queueInfo.target} BPM · ±{queueInfo.tolerance_pct.toFixed(1)}%
              {queueInfo.tracks.length < queueSize ? ` · ${queueInfo.tracks.length}/${queueSize}` : ""}
            </span>
          )}
          <span style={{ marginLeft: "auto", display: "flex", alignItems: "baseline", gap: 10 }}>
            {queueTrack?.artist && (
              <button
                className="btn btn-bare btn-sm"
                style={{ padding: 0, fontSize: 11, color: similarOpen ? "var(--accent-2)" : "var(--muted)" }}
                onClick={() => setSimilarOpen((o) => !o)}
                aria-expanded={similarOpen}
                title={`Tracks similar to ${queueTrack.artist} — in-library matches queue onto this run's cadence`}
              >≈ Similar</button>
            )}
            <Link to="/settings#sec-run" style={{ fontSize: 11, color: "var(--accent-2)" }}>Settings</Link>
          </span>
        </div>
        {similarOpen && queueTrack?.artist && (
          <div style={{ display: "flex", flexDirection: "column", maxHeight: 280, borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
            <QueueSimilar artist={queueTrack.artist} onClose={() => setSimilarOpen(false)} />
          </div>
        )}
        <div style={listStyle}>
          {player.orderedQueue.length === 0 && (
            <div style={{ padding: 16, fontSize: 12, color: "var(--muted)", textAlign: "center" }}>
              No queue yet — set a target and hit Start run.
            </div>
          )}
          {player.orderedQueue.map((t, i) => {
            const starred = starredByPath.get(t.path);
            const disliked = dislikedPaths.has(t.path);
            const tFolded = t.bpm ? fold(t.bpm, target, octave) : null;
            const tRate = t.bpm && lockOn ? lockRate(t.bpm, liveLock) : 1;
            const isCurrentRow = i === player.orderPos;
            return (
              <div key={`${t.path}-${i}`} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 14px", borderBottom: "1px solid var(--border)", background: isCurrentRow ? "var(--row-hover)" : "transparent" }}>
                {starred !== undefined && (
                  <Star on={starred} onToggle={() => toggleStar(t.path, starred)} />
                )}
                <Dislike on={disliked} onToggle={() => toggleDislike(t.path, disliked)} />
                <button
                  style={{ flex: 1, minWidth: 0, textAlign: "left", background: "none", border: "none", cursor: "pointer", color: "inherit", padding: 0 }}
                  onClick={() => player.jumpTo(i)}
                  title={`Play ${t.title}`}
                >
                  <span style={{ display: "block", fontSize: 13, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {isCurrentRow && <span style={{ color: "var(--accent-2)", marginRight: 6 }}>▶</span>}
                    {t.title}
                  </span>
                  {t.artist && <span style={{ display: "block", fontSize: 11, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", marginTop: 2 }}>{t.artist}</span>}
                </button>
                {t.bpm != null && tFolded != null && (
                  <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--muted)", flexShrink: 0, textAlign: "right" }} title="native BPM · octave · stretch → what you hear">
                    {Math.round(t.bpm)} {foldLabel(t.bpm, tFolded)}
                    {lockOn && <span> {tRate.toFixed(2)}×</span>}
                    <span> → <span style={{ color: "var(--accent-2)", fontWeight: 600 }}>{Math.round(tFolded * tRate)}</span></span>
                  </span>
                )}
              </div>
            );
          })}
        </div>
      </div>
    );
  };

  // Mode toggle: mobile folds the queue + tap-tempo in as tabs (no room for a
  // side panel or a persistent tap pad); desktop drops the queue tab (the queue
  // owns a column) and keeps tap-tempo permanently in the info column. Uses the
  // shared design-system .segmented control so the switcher matches the ones on
  // Library and Settings.
  const modeLabel = (m: "presets" | "steps" | "queue" | "tap") =>
    m === "presets" ? "Presets" : m === "steps" ? "± Steps" : m === "tap" ? "Tap" : "Queue";
  const modeToggle = (opts: readonly ("presets" | "steps" | "queue" | "tap")[], active: string) => (
    <div className="segmented" style={{ display: "flex", margin: "0 auto 12px", width: "fit-content", flexWrap: "wrap" }}>
      {opts.map((m) => (
        <button
          key={m}
          className={"segmented-btn " + (active === m ? "active" : "")}
          style={{ minWidth: 62 }}
          onClick={() => setMode(m)}
          aria-pressed={active === m}
        >
          {modeLabel(m)}
        </button>
      ))}
    </div>
  );

  const tapControl = current ? (
    <TapTempoControl locked={!!player.tempoLock} nativeBpm={detail?.bpm ?? current.bpm ?? null} onSave={saveTappedBpm} />
  ) : (
    <div style={{ border: "1px solid var(--border)", borderRadius: 12, padding: "11px 13px", background: "var(--surface)", fontSize: 12, color: "var(--muted)", textAlign: "center" }}>
      Play a track to tap and lock its BPM.
    </div>
  );

  if (desktop) {
    // Desktop cockpit: the shared page header spans the top; below it, the
    // player column (a two-column cockpit — track info left, target controls
    // right — over the full-width transport) sits beside the full-height run
    // queue. Build/end moved up into the header, so the controls column is just
    // target + presets + steps.
    return (
      <div className="run-desktop">
        {glowLayer}
        {pageHeader}
        <div className="run-desktop-body">
          <div className="run-player-col">
            <div className="run-cockpit">
              <div className="run-cockpit-info">
                {nowPlaying}
                {trackDetails}
                <div style={{ marginTop: 14 }}>{tapControl}</div>
              </div>
              <div className="run-cockpit-controls">
                {targetBlock}
                {presetsGrid}
                {stepsRow}
                {buildMessages}
              </div>
            </div>
            {transport}
          </div>
          <aside className="run-queue-col">{renderQueuePanel(true)}</aside>
        </div>
        {lyricsDrawer}
      </div>
    );
  }

  return (
    <div>
      {glowLayer}
      {pageHeader}
      {mode !== "queue" && nowPlaying}
      {targetBlock}
      {modeToggle(["presets", "steps", "tap", "queue"], mode)}
      {mode === "queue" ? renderQueuePanel(false)
        : mode === "tap" ? tapControl
        : mode === "steps" ? stepsRow
        : presetsGrid}
      {buildMessages}
      {transport}
      {lyricsDrawer}
    </div>
  );
}
