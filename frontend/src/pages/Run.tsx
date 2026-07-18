import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { usePlayer, lockRate } from "../lib/player";
import { useMiniPlayer } from "../lib/miniPlayer";
import type { AudioQuality, RunPlaylistOption, RunQueueResponse, SettingsMap, TrackDetailResponse } from "../lib/types";
import { useTapTempo } from "../hooks/useTapTempo";
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
const SOURCE_KEY = "bpm.run.source";   // "library" | "pl:<id>"

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

/** Compact one-line audio-quality summary, e.g. "FLAC · 16-bit/44.1 kHz" or
 *  "MP3 · 320 kbps · 44.1 kHz". Returns null when nothing useful is known. */
function fmtQuality(q: AudioQuality | null | undefined): string | null {
  if (!q) return null;
  const parts: string[] = [];
  if (q.format) parts.push(q.format);
  const depthRate: string[] = [];
  if (q.bits_per_sample) depthRate.push(`${q.bits_per_sample}-bit`);
  if (q.sample_rate) depthRate.push(`${(q.sample_rate / 1000).toFixed(1)} kHz`);
  // Lossless copies are best described by depth/sample-rate; bitrate is huge
  // and uninformative. Lossy copies lead with the bitrate instead.
  if (!q.lossless && q.bitrate) parts.push(`${Math.round(q.bitrate / 1000)} kbps`);
  if (depthRate.length) parts.push(depthRate.join("/"));
  return parts.length ? parts.join(" · ") : null;
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

/** The run cockpit's big cover. Holds a fixed square box (width capped to the
 *  column, aspect-ratio keeps it square) and swaps the artwork only once the
 *  next track's image has decoded — so a track change never blanks the cover,
 *  which previously collapsed the box and jumped the whole cockpit layout.
 *  Falls back to the ♪ placeholder when a cover 404s. */
function RunCover({ path, coverSize, onClick, onMouseEnter, onMouseLeave, ariaLabel, ariaPressed, title, children }: {
  path: string;
  coverSize: string;
  onClick?: () => void;
  onMouseEnter?: () => void;
  onMouseLeave?: () => void;
  ariaLabel?: string;
  ariaPressed?: boolean;
  title?: string;
  children?: React.ReactNode;   // absolute overlays (pop-out hint, source chip)
}) {
  const src = (p: string) => `/api/track/cover?path=${encodeURIComponent(p)}`;
  // `shown` is the path whose art is currently painted; it only advances to the
  // new `path` after that image has finished loading (or errored).
  const [shown, setShown] = useState(path);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (path === shown) return;
    let cancelled = false;
    const img = new Image();
    img.onload = () => { if (!cancelled) { setShown(path); setFailed(false); } };
    img.onerror = () => { if (!cancelled) { setShown(path); setFailed(true); } };
    img.src = src(path);
    return () => { cancelled = true; };
  }, [path, shown]);

  // A fixed-aspect square box, centered in its (definite-width) parent via
  // margin:auto, with the image absolutely filling it. width:min(size,100%)
  // resolves fine here because the parent is a full-width flex/block, not a
  // shrink-to-fit inline box — so it never collapses (the 2.6.6 bug) and the
  // aspect-ratio always wins (no wide/off-square art), while the click target
  // and any overlays share the exact cover footprint.
  const box: React.CSSProperties = {
    position: "relative", display: "block", width: `min(${coverSize}, 100%)`,
    aspectRatio: "1 / 1", margin: "0 auto", borderRadius: 20, overflow: "hidden",
    boxShadow: "0 18px 50px -18px rgba(0,0,0,0.6)", background: "var(--surface)",
    border: "none", padding: 0, cursor: onClick ? "pointer" : "default",
  };
  const inner = (
    <>
      {failed ? (
        <span className="art-thumb" style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", fontSize: 48 }} aria-hidden>♪</span>
      ) : (
        <img src={src(shown)} alt="" onError={() => setFailed(true)} style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover", display: "block" }} />
      )}
      {children}
    </>
  );
  return onClick ? (
    <button type="button" style={box} onClick={onClick} onMouseEnter={onMouseEnter} onMouseLeave={onMouseLeave} aria-label={ariaLabel} aria-pressed={ariaPressed} title={title}>
      {inner}
    </button>
  ) : (
    <div style={box} title={title}>{inner}</div>
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

  // Fixed height so the card doesn't jump between its states — the locked
  // warning, the tap UI, and the post-save message all reserve the same box
  // (tallest state ≈ unlocked + save message).
  const boxStyle: React.CSSProperties = {
    border: "1px solid var(--border)", borderRadius: 12,
    padding: "11px 13px", background: "var(--surface)", minHeight: 226,
  };
  const capLabel: React.CSSProperties = {
    fontFamily: "var(--mono)", fontSize: 10, letterSpacing: "0.1em",
    textTransform: "uppercase", color: "var(--muted)",
  };

  if (locked) {
    return (
      <div style={{ ...boxStyle, textAlign: "center", display: "flex", flexDirection: "column", justifyContent: "center" }}>
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
      {/* Always reserved so the save/error message doesn't shift the footer. */}
      <div style={{ minHeight: 16, marginTop: 9, fontSize: 12, textAlign: "center", color: msg ? (msg.ok ? "var(--ok-fg)" : "var(--err-fg)") : undefined }}>
        {msg?.text}
      </div>
      <div style={{ marginTop: 7, fontSize: 10, color: "var(--muted)", textAlign: "center" }}>Resets after 3s of silence</div>
    </div>
  );
}

export default function Run() {
  useTitle("Run");
  // Run-only role: no tap-tempo (writes tags), no "similar" (reaches Deezer /
  // grab), no links out to pages the player can't open. The backend enforces
  // the same limits; this just keeps the UI honest.
  const { role } = useAuth();
  const playerMode = role === "player";
  const player = usePlayer();
  const mini = useMiniPlayer();
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
  // Initialize from the restored tempo lock, not a blind `true`: a run restored
  // from a previous session (queue persisted in the player) keeps whether its
  // lock was on, so the lock button + native→BPM readout match what's actually
  // playing. A fresh session (no restored queue) keeps the default-on intent.
  const [lockOn, setLockOn] = useState(
    () => player.orderedQueue.length === 0 || player.tempoLock != null);
  // Desktop only: the tap card is hidden behind a Cover/Tap toggle and, when
  // shown, swaps into the cover's slot (see nowPlayingDesktop). Mobile keeps its
  // own segmented Tap tab.
  const [desktopTapOpen, setDesktopTapOpen] = useState(false);
  // Hover state for the cover→mini-player affordance (desktop cover overlay).
  const [coverHover, setCoverHover] = useState(false);
  const [lyricsOpen, setLyricsOpen] = useState(false);
  const [similarOpen, setSimilarOpen] = useState(false);
  const [queueInfo, setQueueInfo] = useState<RunQueueResponse | null>(null);
  const [building, setBuilding] = useState(false);
  const [buildErr, setBuildErr] = useState("");
  // Run source: the whole library (default) or a specific playlist ("pl:<id>").
  const [source, setSourceState] = useState<string>(() => localStorage.getItem(SOURCE_KEY) || "library");
  const setSource = (s: string) => { setSourceState(s); localStorage.setItem(SOURCE_KEY, s); };

  const runPlaylistsQ = useQuery({
    queryKey: ["run-playlists"],
    queryFn: () => api.get<{ playlists: RunPlaylistOption[] }>("/api/run/playlists"),
    staleTime: 30_000,
  });
  const runPlaylists = runPlaylistsQ.data?.playlists ?? [];
  const selectedPlaylistId = source.startsWith("pl:") ? Number(source.slice(3)) : null;
  const selectedPlaylist = runPlaylists.find((p) => p.id === selectedPlaylistId) ?? null;
  // A stored selection that no longer exists (deleted playlist) falls back to library.
  useEffect(() => {
    if (selectedPlaylistId != null && runPlaylistsQ.data && !selectedPlaylist) setSource("library");
  }, [selectedPlaylistId, runPlaylistsQ.data, selectedPlaylist]);
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
    // Keep the previous track's detail on screen while the next one loads, so
    // the info column / play-count don't blank and jump on every track change
    // (matches the cover's hold-then-swap).
    placeholderData: keepPreviousData,
  });
  const detail = trackQ.data?.track;
  const quality = fmtQuality(trackQ.data?.quality);
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

  // If the tempo lock switches on while the mobile Tap tab is selected, fall
  // back to Presets — Tap is disabled under the lock, so leaving it selected
  // would strand the view on a card that only tells you to release the lock.
  useEffect(() => {
    if ((running || playerMode) && mode === "tap") setMode("presets");
    // Same reasoning for the desktop Cover/Tap toggle: drop back to the cover.
    if ((running || playerMode) && desktopTapOpen) setDesktopTapOpen(false);
  }, [running, playerMode, mode, desktopTapOpen]);

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
      const scope = selectedPlaylistId != null ? `&playlist=${selectedPlaylistId}` : "";
      const resp = await api.get<RunQueueResponse>(`/api/run/queue?bpm=${target}${scope}`);
      setQueueInfo(resp);
      if (!resp.tracks.length) {
        setBuildErr(selectedPlaylist
          ? `No tracks in "${selectedPlaylist.name}" match this BPM — widen the tolerance in Settings, pick another target, or choose a different source.`
          : "No tracks match this BPM — widen the tolerance in Settings or pick another target.");
        return;
      }
      player.playQueue(
        resp.tracks.map((t) => ({ path: t.path, title: t.title, artist: t.artist, bpm: t.bpm, starred: t.starred, fromPlaylist: t.from_playlist })),
        0, { shuffle: false },
      );
      // Pin the run's source so the mid-run auto-refill stays scoped to it.
      player.setRunSource(selectedPlaylistId);
      player.setTempoLock(lockOn ? liveLock : null);
    } catch (e) {
      setBuildErr(e instanceof Error ? e.message : "Failed to build the queue");
    } finally {
      setBuilding(false);
    }
  }

  function toggleStar(path: string, starred: boolean) {
    const next = !starred;
    // Optimistic, on the queued track itself so it works for auto-refilled
    // tracks too (they never lived in queueInfo). A failed star isn't worth
    // interrupting a run.
    player.setTrackStarred(path, next);
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
  // Source changed since the queue was built (queueInfo.playlist is the source
  // the current queue + its auto-refill are scoped to; null = whole library).
  // The change only takes effect on the next Rebuild, so prompt for it.
  const staleSource = !!queueInfo && (selectedPlaylistId ?? null) !== (queueInfo.playlist ?? null);

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
  // Player mode has a sticky brand bar above the page (the kiosk has no
  // sidebar); reserve its height (matches --player-bar-h) so the mobile layout
  // still fits one screen.
  const bannerReserve = playerMode ? 52 : 0;
  const coverSize = desktop
    // Height-aware: derive the cover from the viewport height minus the fixed
    // cockpit chrome (page header + title/details/toggle + transport) so it
    // shrinks on short screens instead of overflowing, and grows on tall ones.
    // Width is separately capped to the column (see coverImg) so it never
    // exceeds its half of the cockpit.
    ? "clamp(140px, calc(100dvh - 540px), 300px)"
    // Reserve = everything below the cover on mobile (title, target with the
    // flanking Rebuild, mode tabs, controls, transport + tight padding). With
    // the header gone and Rebuild flanking the number (no extra row), 605 is the
    // measured worst case (narrow phone, wrapped title) so it never scrolls.
    : `clamp(64px, calc(100dvh - ${605 + bannerReserve}px), 240px)`;

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
  // Primary action — the only build control. (There's no "End": pause stops
  // playback and the lock toggle beside the target releases the tempo lock.)
  const startRebuildButton = (
    <button className="btn btn-primary" style={{ minHeight: 40, whiteSpace: "nowrap" }} disabled={building} onClick={startRun}>
      {building ? "Building…" : queueInfo || running ? "Rebuild" : "Start run"}
    </button>
  );


  // Desktop shows the full uniform header (title + subtitle + right-aligned
  // action). Mobile has no header at all — the nav already says you're on Run,
  // and the screen is tight — so the cover gets that space and the Start/Rebuild
  // button rides in the mode-toggle row instead (see modeToggle).
  const pageHeader = (
    <PageHeader
      title="Run"
      subtitle="Tempo-matched player — lock every track onto your cadence."
      actions={startRebuildButton}
    />
  );

  // Now playing: cover + title/artist. Split into pieces so the desktop cockpit
  // can swap the cover slot for the tap card (nowPlayingDesktop) without touching
  // the title/artist markup; mobile keeps the composed `nowPlaying`.
  const titleArtist = current && (
    <>
      {playerMode ? (
        <div
          style={{
            fontSize: 18, fontWeight: 600, letterSpacing: "-0.015em", marginBottom: 2,
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          }}
        >
          {current.title}
        </div>
      ) : (
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
      )}
      {current.artist && <div style={{ fontSize: 13, color: "var(--muted)" }}>{current.artist}</div>}
      {detail?.play_count != null && (
        <div style={{ display: "inline-flex", alignItems: "center", gap: 5, fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)", marginTop: 5 }}>
          <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" style={{ flexShrink: 0 }}><polygon points="6,4 20,12 6,20" /></svg>
          {detail.play_count} play{detail.play_count === 1 ? "" : "s"}
        </div>
      )}
    </>
  );
  // The run-source picker is not overlaid on the cover any more (it blocked the
  // artwork and, with a long playlist name, spilled across it). On mobile it now
  // lives in the Queue view — see renderQueuePanel — reachable mid-run without
  // covering anything; pre-run it still shows as the labelled `sourcePicker`
  // block below (no cover is on screen yet then).
  const nowPlaying = current && (
    <div style={{ textAlign: "center", marginBottom: 12 }}>
      <div style={{ marginBottom: 10 }}>
        <RunCover path={current.path} coverSize={coverSize} />
      </div>
      {titleArtist}
    </div>
  );

  // Run source: whole library or a specific playlist. Only rendered when at least
  // one playlist exists, so library-only users never see an empty dropdown.
  const sourcePicker = runPlaylists.length > 0 && (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, justifyContent: "center", flexWrap: "wrap" }}>
        <span style={{ fontFamily: "var(--mono)", fontSize: 10, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--muted)" }}>Source</span>
        <select
          value={source}
          onChange={(e) => setSource(e.target.value)}
          aria-label="Run source"
          style={{ fontSize: 13, padding: "6px 10px", borderRadius: 10, background: "var(--surface)", border: "1px solid var(--border)", color: "var(--text)", maxWidth: 260 }}
        >
          <option value="library">Whole library</option>
          {runPlaylists.map((p) => (
            <option key={p.id} value={`pl:${p.id}`}>{p.name} ({p.available})</option>
          ))}
        </select>
      </div>
      {selectedPlaylist && (
        <div style={{ textAlign: "center", fontSize: 11, color: selectedPlaylist.available === 0 ? "var(--warn-fg)" : "var(--muted)" }}>
          {selectedPlaylist.available === 0
            ? "No runnable tracks here — none matched a local file with a detected BPM."
            : `${selectedPlaylist.available} of ${selectedPlaylist.total} tracks available for runs`}
        </div>
      )}
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
        {/* Mobile: the primary build control flanks the target number as a round
            icon button (mirroring the lock on the right), so it needs no header
            row and the cover keeps the top of the screen. Desktop keeps the
            labelled button in the page header. */}
        {!desktop && (
          <button
            onClick={startRun}
            disabled={building}
            aria-label={building ? "Building queue" : queueInfo || running ? "Rebuild queue" : "Start run"}
            title={building ? "Building…" : queueInfo || running ? "Rebuild the queue for this target" : "Start run — build the queue for this target"}
            style={{
              position: "absolute", right: "100%", top: "50%", transform: "translateY(-50%)", marginRight: 12,
              width: 38, height: 38, borderRadius: 999, display: "inline-flex", alignItems: "center",
              justifyContent: "center", cursor: building ? "default" : "pointer",
              background: "var(--accent)", border: "none", color: "white",
              opacity: building ? 0.55 : 1,
            }}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M23 4v6h-6" />
              <path d="M1 20v-6h6" />
              <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
            </svg>
          </button>
        )}
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
      {staleSource && !staleQueue && (
        <div style={{ textAlign: "center", fontSize: 12, color: "var(--warn-fg)", marginBottom: 6 }}>
          Source changed to {selectedPlaylist ? `“${selectedPlaylist.name}”` : "your whole library"} — hit Rebuild to use it.
        </div>
      )}
      {queueInfo?.topped_up && selectedPlaylist && (
        <div style={{ textAlign: "center", fontSize: 12, color: "var(--muted)", marginBottom: 6 }}>
          “{selectedPlaylist.name}” had too few tracks at this cadence — topped up from your library so the run keeps varied.
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
        value={detail.duration_ms ? fmtTime(detail.duration_ms / 1000) : "—"}
      />
      {quality && <DetailRow label="Quality" value={quality} />}
    </div>
  );

  // Waveform + transport controls (dislike / prev / play / next / lyrics).
  const transport = current && (
    <div className="run-transport" style={{ marginTop: desktop ? 20 : 10, marginBottom: desktop ? 0 : 16 }}>
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
        {!playerMode && (
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
        )}
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
      ? {
          // Absolutely fill the side column (position:relative on .run-queue-col)
          // so the queue never contributes to the row height: the player column
          // alone sets it, the queue's bottom always lines up with the transport,
          // and a long queue scrolls inside the panel instead of the page.
          padding: 0, margin: 0, position: "absolute", inset: 0,
          display: "flex", flexDirection: "column",
        }
      : { padding: 0, marginBottom: 12 };
    const listStyle: React.CSSProperties = fill
      ? { flex: 1, minHeight: 0, overflowY: "auto" }
      // Mobile Queue tab: cap the scroll area so the whole panel (header + the
      // in-panel source picker + list) is no taller than the cover+controls it
      // replaces — otherwise switching to Queue grew the page and forced a
      // scroll. Reserve = target readout + mode toggle + panel header + source
      // row + transport + padding (+ the kiosk brand bar via bannerReserve).
      : { maxHeight: `clamp(120px, calc(100dvh - ${560 + bannerReserve}px), 340px)`, overflowY: "auto" };
    return (
      <div className="card" style={cardStyle}>
        <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap", flexShrink: 0 }}>
          <span style={{ fontWeight: 600, fontSize: 13 }}>Run queue · {player.orderedQueue.length}</span>
          {queueInfo && (
            <span style={{ fontSize: 11, color: "var(--muted)" }}>
              built for {queueInfo.target} BPM · ±{queueInfo.tolerance_pct.toFixed(1)}%
              {player.orderedQueue.length < queueSize ? ` · ${player.orderedQueue.length}/${queueSize}` : ""}
            </span>
          )}
          <span style={{ marginLeft: "auto", display: "flex", alignItems: "baseline", gap: 10 }}>
            {!playerMode && queueTrack?.artist && (
              <button
                className="btn btn-bare btn-sm"
                style={{ padding: 0, fontSize: 11, color: similarOpen ? "var(--accent-2)" : "var(--muted)" }}
                onClick={() => setSimilarOpen((o) => !o)}
                aria-expanded={similarOpen}
                title={`Tracks similar to ${queueTrack.artist} — in-library matches queue onto this run's cadence`}
              >≈ Similar</button>
            )}
            {!playerMode && (
              <Link to="/settings#sec-run" style={{ fontSize: 11, color: "var(--accent-2)" }}>Settings</Link>
            )}
          </span>
        </div>
        {/* Run-source picker — mobile only (desktop keeps it in the controls
            column). Lives here so it's reachable mid-run without covering the
            artwork. Falsy (renders nothing) when the library has no playlists. */}
        {!fill && sourcePicker && (
          <div data-testid="queue-source" style={{ padding: "10px 14px 0", borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
            {sourcePicker}
          </div>
        )}
        {similarOpen && queueTrack?.artist && (
          <div style={{ display: "flex", flexDirection: "column", maxHeight: 280, borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
            <QueueSimilar artist={queueTrack.artist} onClose={() => setSimilarOpen(false)} />
          </div>
        )}
        <div data-testid="queue-list" style={listStyle}>
          {player.orderedQueue.length === 0 && (
            <div style={{ padding: 16, fontSize: 12, color: "var(--muted)", textAlign: "center" }}>
              No queue yet — set a target and hit Start run.
            </div>
          )}
          {player.orderedQueue.map((t, i) => {
            const starred = t.starred;
            const disliked = dislikedPaths.has(t.path);
            const tFolded = t.bpm ? fold(t.bpm, target, octave) : null;
            const tRate = t.bpm && lockOn ? lockRate(t.bpm, liveLock) : 1;
            const isCurrentRow = i === player.orderPos;
            // In a playlist run, tracks added to fill the queue from the library
            // (not in the playlist itself) are dimmed + italic so they read as
            // clearly secondary to the playlist's own tracks. The playing row is
            // kept full-opacity so "now playing" is never the faded one.
            const fromLibrary = player.runSource != null && t.fromPlaylist === false;
            return (
              <div key={`${t.path}-${i}`} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 14px", borderBottom: "1px solid var(--border)", background: isCurrentRow ? "var(--row-hover)" : "transparent", opacity: fromLibrary && !isCurrentRow ? 0.5 : 1 }}>
                {starred !== undefined && (
                  <Star on={starred} onToggle={() => toggleStar(t.path, starred)} />
                )}
                <Dislike on={disliked} onToggle={() => toggleDislike(t.path, disliked)} />
                <button
                  style={{ flex: 1, minWidth: 0, textAlign: "left", background: "none", border: "none", cursor: "pointer", color: "inherit", padding: 0 }}
                  onClick={() => player.jumpTo(i)}
                  title={fromLibrary ? `Play ${t.title} — added from your library` : `Play ${t.title}`}
                >
                  <span style={{ display: "block", fontSize: 13, fontWeight: 500, fontStyle: fromLibrary ? "italic" : "normal", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
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
    m === "presets" ? "Presets" : m === "steps" ? "Steps" : m === "tap" ? "Tap" : "Queue";
  const modeToggle = (opts: readonly ("presets" | "steps" | "queue" | "tap")[], active: string) => (
    <div className="segmented" style={{ display: "flex", margin: "0 auto 12px", width: "fit-content", flexWrap: "wrap" }}>
      {opts.map((m) => {
        // Tap needs the track at its true speed, so it's useless (and its warning
        // card just adds scroll) while the tempo lock is stretching playback.
        const disabled = m === "tap" && running;
        return (
          <button
            key={m}
            className={"segmented-btn " + (active === m ? "active" : "")}
            style={{ minWidth: 62, ...(disabled ? { opacity: 0.4, cursor: "not-allowed" } : null) }}
            onClick={() => setMode(m)}
            disabled={disabled}
            aria-pressed={active === m}
            title={disabled ? "Release the tempo lock to tap a track's real BPM" : undefined}
          >
            {modeLabel(m)}
          </button>
        );
      })}
    </div>
  );

  const tapControl = current ? (
    <TapTempoControl locked={!!player.tempoLock} nativeBpm={detail?.bpm ?? current.bpm ?? null} onSave={saveTappedBpm} />
  ) : (
    <div style={{ border: "1px solid var(--border)", borderRadius: 12, padding: "11px 13px", background: "var(--surface)", fontSize: 12, color: "var(--muted)", textAlign: "center" }}>
      Play a track to tap and lock its BPM.
    </div>
  );

  // Desktop info column: the cover slot swaps to the tap card when Tap is toggled
  // on, and both states reserve the same slot height so the cockpit row never
  // resizes. The Cover/Tap segmented toggle sits below the details (rendered in
  // both states → constant height). Tap is disabled while a run's tempo lock is
  // stretching playback (you'd tap the shifted tempo, not the real BPM), same as
  // the mobile Tap tab.
  const desktopTapActive = desktopTapOpen && !playerMode;
  // The cover doubles as the mini-player toggle: clicking it pops out (or
  // closes) the floating Document PiP window, with a full-cover overlay hint
  // instead of a separate corner button. Desktop only (it only ever renders
  // inside nowPlayingDesktop) and Chromium-only (mini.supported — Document PiP
  // is unavailable elsewhere), so the affordance only shows where it can work.
  // Where PiP is unsupported the cover renders as a plain (non-clickable) image.
  // Overlay hint shown over the cover (fades in on hover, stays lit while the
  // floating window is open). pointer-events:none so clicks reach the cover
  // button beneath it.
  const popoutOverlay = (
    <span
      aria-hidden
      style={{
        position: "absolute", inset: 0, pointerEvents: "none",
        display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 9,
        background: mini.isOpen ? "rgba(0,0,0,0.34)" : "rgba(0,0,0,0.46)",
        opacity: mini.isOpen || coverHover ? 1 : 0,
        transition: "opacity 0.18s ease",
      }}
    >
      <span
        style={{
          width: 46, height: 46, borderRadius: 999, display: "inline-flex", alignItems: "center", justifyContent: "center",
          backdropFilter: "blur(6px)", WebkitBackdropFilter: "blur(6px)",
          background: mini.isOpen ? "var(--accent)" : "rgba(255,255,255,0.16)",
          border: `1px solid ${mini.isOpen ? "var(--accent-border)" : "rgba(255,255,255,0.30)"}`,
          color: "white",
        }}
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="4" width="18" height="14" rx="2" />
          <rect x="11" y="9" width="8" height="6" rx="1" fill="currentColor" stroke="none" />
        </svg>
      </span>
      <span style={{ fontSize: 11.5, fontWeight: 600, letterSpacing: "0.01em", color: "white", textShadow: "0 1px 4px rgba(0,0,0,0.7)" }}>
        {mini.isOpen ? "Floating player open" : "Pop out mini player"}
      </span>
    </span>
  );
  const coverWithPopout = current && (mini.supported ? (
    <RunCover
      path={current.path}
      coverSize={coverSize}
      onClick={mini.toggle}
      onMouseEnter={() => setCoverHover(true)}
      onMouseLeave={() => setCoverHover(false)}
      ariaPressed={mini.isOpen}
      ariaLabel={mini.isOpen ? "Close the floating mini player" : "Open the floating mini player"}
      title={mini.isOpen ? "Close the floating player" : "Pop out a floating mini player — cadence + transport, always on top"}
    >
      {popoutOverlay}
    </RunCover>
  ) : <RunCover path={current.path} coverSize={coverSize} />);
  const nowPlayingDesktop = current && (
    <div style={{ textAlign: "center", marginBottom: 12 }}>
      {/* The slot hugs the cover (auto) by default, so short screens don't waste
          height on an oversized box. Only when Tap is active do we reserve the
          tap card's 226px minHeight (see TapTempoControl). On normal/tall screens
          coverSize ≥ 226, so cover ⇄ tap swaps without a shift; on short screens
          (coverSize < 226) toggling to Tap grows the slot — an acceptable trade
          for keeping the default cover view compact. */}
      <div style={{ display: "flex", justifyContent: "center", alignItems: "center", marginBottom: 10, height: desktopTapActive ? `max(${coverSize}, 226px)` : "auto" }}>
        {desktopTapActive
          ? <div style={{ width: "100%" }}>{tapControl}</div>
          : coverWithPopout}
      </div>
      {titleArtist}
    </div>
  );
  const coverTapToggle = !playerMode && (
    <div className="segmented" style={{ display: "flex", margin: "14px auto 0", width: "fit-content" }}>
      {([false, true] as const).map((tapView) => {
        const disabled = tapView && running;
        return (
          <button
            key={tapView ? "tap" : "cover"}
            className={"segmented-btn " + (desktopTapOpen === tapView ? "active" : "")}
            style={{ minWidth: 62, ...(disabled ? { opacity: 0.4, cursor: "not-allowed" } : null) }}
            onClick={() => setDesktopTapOpen(tapView)}
            disabled={disabled}
            aria-pressed={desktopTapOpen === tapView}
            title={disabled ? "Release the tempo lock to tap a track's real BPM" : undefined}
          >
            {tapView ? "Tap" : "Cover"}
          </button>
        );
      })}
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
                {nowPlayingDesktop}
                {trackDetails}
                {coverTapToggle}
              </div>
              <div className="run-cockpit-controls">
                {sourcePicker}
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
    // Player mode fills the viewport (run-mobile-fill) so the transport pins to
    // the bottom and the cockpit above distributes the leftover space; admin
    // mobile keeps its normal top-down flow (the wrapper class is inert there).
    <div className={playerMode ? "run-mobile-fill" : undefined}>
      {glowLayer}
      <div className="run-mobile-body">
        {mode !== "queue" && nowPlaying}
        {/* Tap needs the whole tap pad on screen at once; the target readout (its
            big number + native pill + the build/lock buttons) is dead weight while
            tapping — the lock is off in this mode anyway — so drop it here to keep
            the pad within one screen without scrolling. */}
        {/* Source picker as a full row only before a run starts (no cover yet,
            so there's room). Once a track is playing, switching the source lives
            in the Queue view instead (see renderQueuePanel) so it never covers
            the artwork. */}
        {!current && mode !== "tap" && mode !== "queue" && sourcePicker}
        {mode !== "tap" && targetBlock}
        {modeToggle(playerMode ? ["presets", "steps", "queue"] : ["presets", "steps", "tap", "queue"], mode)}
        {mode === "queue" ? renderQueuePanel(false)
          : mode === "tap" ? tapControl
          : mode === "steps" ? stepsRow
          : presetsGrid}
        {buildMessages}
      </div>
      {transport}
      {lyricsDrawer}
    </div>
  );
}
