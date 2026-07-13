import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { audioUrl } from "./api";

export interface PlayerTrack {
  path: string;
  title: string;
  artist?: string;
  bpm?: number | null;
}

/** Run-mode tempo lock: stretch every queued track onto one target BPM. */
export interface TempoLock {
  target: number;          // cadence BPM the queue should land on
  octave: boolean;         // fold ×½/×1/×2 before stretching
  stretchLimitPct: number; // clamp for how far playbackRate may move from 1
}

/** playbackRate for a track under a tempo lock: fold its BPM to the octave
 *  closest to the target, stretch the remainder, clamp to the stretch limit.
 *  Tracks without a BPM play at native speed. */
export function lockRate(trackBpm: number | null | undefined, lock: TempoLock | null): number {
  if (!lock || !trackBpm) return 1;
  const cands = lock.octave ? [trackBpm, trackBpm / 2, trackBpm * 2] : [trackBpm];
  const folded = cands.reduce((a, b) =>
    Math.abs(lock.target / b - 1) < Math.abs(lock.target / a - 1) ? b : a);
  const lim = lock.stretchLimitPct / 100;
  return Math.min(1 + lim, Math.max(1 - lim, lock.target / folded));
}

export type RepeatMode = "off" | "all" | "one";

const FADE_MS = 250;

interface PlayerState {
  current: PlayerTrack | null;
  playing: boolean;
  error: string | null;
  audioRef: RefObject<HTMLAudioElement>;
  // Queue
  queue: PlayerTrack[];
  queueIndex: number;      // index of current track within queue (-1 if none)
  orderedQueue: PlayerTrack[];  // queue in playback order (respects shuffle)
  orderPos: number;        // position of the current track within orderedQueue
  hasQueue: boolean;       // more than one track queued
  shuffle: boolean;
  repeat: RepeatMode;
  previewing: boolean;     // a detail/compare preview is ducking the main queue
  volume: number;
  setVolume(v: number): void;
  tempoLock: TempoLock | null;
  setTempoLock(lock: TempoLock | null): void;
  play(track: PlayerTrack): void;                                  // one-off
  playQueue(tracks: PlayerTrack[], startIndex?: number, opts?: { shuffle?: boolean }): void;
  enqueue(track: PlayerTrack): void;   // append to the queue
  playNext(track: PlayerTrack): void;  // insert right after the current track
  preview(track: PlayerTrack): void;   // duck the queue, play track, resume on end/leave
  endPreview(): void;                  // fade back to the saved queue track
  next(): void;
  prev(): void;
  jumpTo(orderPos: number): void;      // play a specific queue position
  removeAt(orderPos: number): void;    // drop a track from the queue
  moveAt(orderPos: number, dir: -1 | 1): void;  // reorder up/down
  toggleShuffle(): void;
  cycleRepeat(): void;
  toggle(): void;
  stop(): void;
  isCurrent(path: string): boolean;
}

const Ctx = createContext<PlayerState | null>(null);

const SAVE_KEY = "bpm.player";
interface SavedPlayer {
  queue: PlayerTrack[]; order: number[]; pos: number;
  shuffle: boolean; repeat: RepeatMode; volume: number;
  time?: number; playing?: boolean;
  tempoLock?: TempoLock | null;
}

function loadSaved(): SavedPlayer | null {
  try {
    const s = JSON.parse(localStorage.getItem(SAVE_KEY) || "null");
    if (s && Array.isArray(s.queue) && s.queue.length && Array.isArray(s.order)) return s;
  } catch { /* ignore */ }
  return null;
}

/** Fisher-Yates shuffle of a copy (browser Math.random). */
function shuffled(indices: number[]): number[] {
  const a = indices.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

/** One <audio> element for the whole app, so playback survives route changes. */
export function PlayerProvider({ children }: { children: ReactNode }) {
  const audioRef = useRef<HTMLAudioElement>(null);
  // Restore the last queue from localStorage, at its saved position. If it was
  // playing, try to resume — the browser's autoplay policy may veto that, in
  // which case it stays paused at the right position.
  const [saved] = useState<SavedPlayer | null>(loadSaved);
  const [current, setCurrent] = useState<PlayerTrack | null>(() =>
    saved && saved.pos >= 0 ? (saved.queue[saved.order[saved.pos]] ?? null) : null);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pendingPlay = useRef(!!(current && saved?.playing));
  // Set before a setCurrent to seek the freshly-loaded track (used to restore a
  // preview's saved position) and to fade the next play in from silence.
  const seekTarget = useRef<number | null>(
    current && saved?.time && isFinite(saved.time) ? saved.time : null);
  const fadeIn = useRef(pendingPlay.current);

  // Queue state. `order` holds queue indices in playback order (so shuffle can
  // be toggled without losing the current track); `pos` is the position within
  // `order`. queueIndex = order[pos].
  const [queue, setQueue] = useState<PlayerTrack[]>(() => saved?.queue ?? []);
  const [order, setOrder] = useState<number[]>(() => saved?.order ?? []);
  const [pos, setPos] = useState(() => saved?.pos ?? -1);
  const [shuffle, setShuffle] = useState(() => saved?.shuffle ?? false);
  const [repeat, setRepeat] = useState<RepeatMode>(() => saved?.repeat ?? "off");
  const [tempoLock, setTempoLock] = useState<TempoLock | null>(() => saved?.tempoLock ?? null);
  const [volume, setVolumeState] = useState(() => saved?.volume ?? 1);
  const volumeRef = useRef(volume);
  const mutePrev = useRef(saved?.volume || 1);  // volume to restore when unmuting

  // Ducking preview: while active, the queue track + position + play-state are
  // stashed here and restored when the preview ends or the user leaves.
  const previewSaved = useRef<{ track: PlayerTrack; time: number; wasPlaying: boolean } | null>(null);
  const [previewing, setPreviewing] = useState(false);

  // Mirror queue state into a ref so the (once-attached) "ended" handler and the
  // stable next/prev callbacks always read the latest values.
  const nav = useRef({ queue, order, pos, repeat, shuffle, tempoLock });
  useEffect(() => { nav.current = { queue, order, pos, repeat, shuffle, tempoLock }; }, [queue, order, pos, repeat, shuffle, tempoLock]);

  // Persist the queue + position + play-state so a reload restores playback
  // where it left off.
  const persist = useCallback(() => {
    try {
      const { queue, order, pos, shuffle, repeat } = nav.current;
      if (!queue.length) { localStorage.removeItem(SAVE_KEY); return; }
      const a = audioRef.current;
      const pv = previewSaved.current;
      // While a preview is ducking the queue, save the queue track's saved
      // position/state — not the preview's.
      const time = pv ? pv.time : a?.currentTime || 0;
      const isPlaying = pv ? pv.wasPlaying : !!a && !a.paused;
      localStorage.setItem(SAVE_KEY, JSON.stringify({
        queue, order, pos, shuffle, repeat,
        volume: volumeRef.current, time, playing: isPlaying,
        tempoLock: nav.current.tempoLock,
      }));
    } catch { /* ignore */ }
  }, []);
  useEffect(persist, [queue, order, pos, shuffle, repeat, volume, tempoLock, persist]);

  // The state effect above can't see time ticking, so capture the exact
  // position when the page is hidden or unloaded (refresh, tab close, mobile
  // app switch) — pagehide + visibilitychange cover desktop and mobile.
  useEffect(() => {
    const onHide = () => { if (document.visibilityState === "hidden") persist(); };
    window.addEventListener("pagehide", persist);
    document.addEventListener("visibilitychange", onHide);
    return () => {
      window.removeEventListener("pagehide", persist);
      document.removeEventListener("visibilitychange", onHide);
    };
  }, [persist]);

  const setVolume = useCallback((v: number) => {
    const vol = Math.max(0, Math.min(1, v));
    volumeRef.current = vol;
    setVolumeState(vol);
    if (audioRef.current) audioRef.current.volume = vol;
  }, []);

  // Volume ramp for fades. rAF animates the volume for smoothness, but the
  // completion (`done`) and the final volume are driven by a setTimeout so the
  // sequence never stalls when rAF is throttled in a hidden/occluded tab —
  // otherwise a preview could fail to swap or resume playing silently.
  const rampRef = useRef<number | null>(null);
  const rampTimer = useRef<number | null>(null);
  const rampVolume = useCallback((from: number, to: number, ms: number, done?: () => void) => {
    const a = audioRef.current;
    if (!a) { done?.(); return; }
    if (rampRef.current != null) cancelAnimationFrame(rampRef.current);
    if (rampTimer.current != null) clearTimeout(rampTimer.current);
    const start = performance.now();
    a.volume = from;
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / ms);
      a.volume = from + (to - from) * t;
      if (t < 1) rampRef.current = requestAnimationFrame(step);
    };
    rampRef.current = requestAnimationFrame(step);
    rampTimer.current = window.setTimeout(() => {
      if (rampRef.current != null) { cancelAnimationFrame(rampRef.current); rampRef.current = null; }
      a.volume = to;
      done?.();
    }, ms);
  }, []);

  // Load the source whenever the current track changes; seek + fade-in + auto-play as requested.
  useEffect(() => {
    const a = audioRef.current;
    if (!a || !current) return;
    setError(null);
    a.src = audioUrl(current.path);
    a.load();
    const seekTo = seekTarget.current; seekTarget.current = null;
    const shouldPlay = pendingPlay.current; pendingPlay.current = false;
    const doFadeIn = fadeIn.current; fadeIn.current = false;
    const begin = () => {
      if (seekTo != null && isFinite(seekTo)) { try { a.currentTime = seekTo; } catch { /* ignore */ } }
      if (shouldPlay) {
        a.volume = doFadeIn ? 0 : volumeRef.current;
        a.play().catch(() => {});
        if (doFadeIn) rampVolume(0, volumeRef.current, FADE_MS);
      } else {
        a.volume = volumeRef.current;
      }
    };
    if (seekTo != null) {
      a.addEventListener("loadedmetadata", begin, { once: true });
      return () => a.removeEventListener("loadedmetadata", begin);
    }
    begin();
  }, [current?.path, rampVolume]);

  // Tempo lock: stretch the current track onto the target BPM (pitch preserved).
  // defaultPlaybackRate too — load() resets playbackRate to it on track change,
  // and this effect runs after the load effect above (declaration order).
  useEffect(() => {
    const a = audioRef.current;
    if (!a) return;
    const rate = lockRate(current?.bpm, tempoLock);
    a.defaultPlaybackRate = rate;
    a.playbackRate = rate;
    a.preservesPitch = true;
    (a as HTMLAudioElement & { webkitPreservesPitch?: boolean }).webkitPreservesPitch = true;
  }, [current, tempoLock]);

  // Reserve space at the bottom of the page for the bar while a track is loaded.
  useEffect(() => {
    document.body.classList.toggle("has-player", !!current);
  }, [current]);

  const cancelRamp = () => {
    if (rampRef.current != null) { cancelAnimationFrame(rampRef.current); rampRef.current = null; }
    if (rampTimer.current != null) { clearTimeout(rampTimer.current); rampTimer.current = null; }
  };
  // Called by every take-over path (play/playQueue/next/prev/stop) — also cancels
  // any in-flight fade so a pending swap can't clobber the new track.
  const clearPreview = () => { cancelRamp(); previewSaved.current = null; setPreviewing(false); };

  const goToPos = useCallback((newPos: number) => {
    const { queue, order } = nav.current;
    const track = queue[order[newPos]];
    if (!track) return;
    setPos(newPos);
    pendingPlay.current = true;
    setCurrent(track);
  }, []);

  const next = useCallback((auto = false) => {
    clearPreview();                       // explicit queue action takes over (dec 6)
    const a = audioRef.current;
    const { order, pos, repeat } = nav.current;
    if (auto && repeat === "one") {
      if (a) { a.currentTime = 0; a.play().catch(() => {}); }
      return;
    }
    if (order.length === 0) return;
    let np = pos + 1;
    if (np >= order.length) {
      if (repeat === "all") np = 0;
      else return;               // end of queue, no repeat → stop advancing
    }
    goToPos(np);
  }, [goToPos]);

  const prev = useCallback(() => {
    clearPreview();
    const a = audioRef.current;
    const { order, pos, repeat } = nav.current;
    // Standard behaviour: restart the current track if we're past 3s.
    if (a && a.currentTime > 3) { a.currentTime = 0; return; }
    if (order.length === 0) { if (a) a.currentTime = 0; return; }
    let np = pos - 1;
    if (np < 0) {
      if (repeat === "all") np = order.length - 1;
      else { if (a) a.currentTime = 0; return; }
    }
    goToPos(np);
  }, [goToPos]);

  const endPreview = useCallback(() => {
    const saved = previewSaved.current;
    if (!saved) return;                    // not previewing
    previewSaved.current = null;
    setPreviewing(false);
    const restore = () => {
      seekTarget.current = saved.time;
      pendingPlay.current = saved.wasPlaying;   // resume only if it was playing (dec 3/8)
      fadeIn.current = saved.wasPlaying;
      setCurrent(saved.track);
    };
    const a = audioRef.current;
    if (a && !a.paused) rampVolume(a.volume, 0, FADE_MS, restore);
    else restore();
  }, [rampVolume]);

  // Audio element event wiring (attached once).
  useEffect(() => {
    const a = audioRef.current;
    if (!a) return;
    const onPlay = () => { setPlaying(true); setError(null); };
    const onPause = () => setPlaying(false);
    // Preview ended → resume the queue (dec 2); otherwise auto-advance.
    const onEnded = () => { setPlaying(false); if (previewSaved.current) endPreview(); else next(true); };
    // A failed stream (404, decode error, network drop) would otherwise leave
    // the UI stuck in a "playing" state with no feedback — surface it instead.
    // The saved queue track is kept so leaving still restores it (dec 9).
    const onError = () => { setPlaying(false); setError("Playback failed — the file may be missing or unsupported."); };
    a.addEventListener("play", onPlay);
    a.addEventListener("pause", onPause);
    a.addEventListener("ended", onEnded);
    a.addEventListener("error", onError);
    return () => {
      a.removeEventListener("play", onPlay);
      a.removeEventListener("pause", onPause);
      a.removeEventListener("ended", onEnded);
      a.removeEventListener("error", onError);
    };
  }, [next, endPreview]);

  // Media Session: lock-screen / headset / notification controls (key for the
  // PWA running use case — the phone is locked mid-run). Metadata mirrors the
  // current track; cover art 404s are fine (the OS just shows no artwork).
  useEffect(() => {
    if (!("mediaSession" in navigator)) return;
    navigator.mediaSession.metadata = current
      ? new MediaMetadata({
          title: current.title,
          artist: current.artist ?? "",
          artwork: [{ src: `/api/track/cover?path=${encodeURIComponent(current.path)}`, sizes: "512x512" }],
        })
      : null;
  }, [current]);

  useEffect(() => {
    if (!("mediaSession" in navigator)) return;
    navigator.mediaSession.playbackState = current ? (playing ? "playing" : "paused") : "none";
  }, [current, playing]);

  useEffect(() => {
    if (!("mediaSession" in navigator)) return;
    const ms = navigator.mediaSession;
    ms.setActionHandler("play", () => audioRef.current?.play().catch(() => {}));
    ms.setActionHandler("pause", () => audioRef.current?.pause());
    ms.setActionHandler("previoustrack", () => prev());
    ms.setActionHandler("nexttrack", () => next(false));
    return () => {
      for (const a of ["play", "pause", "previoustrack", "nexttrack"] as MediaSessionAction[]) {
        ms.setActionHandler(a, null);
      }
    };
  }, [prev, next]);

  const play = useCallback((track: PlayerTrack) => {
    // One-off play (e.g. a library/search row): becomes a single-item queue so
    // prev/next are inert and shuffle has no stale context. Takes over any preview.
    clearPreview();
    setQueue([track]);
    setOrder([0]);
    setPos(0);
    setCurrent((prev) => {
      if (prev?.path === track.path) {
        audioRef.current?.play().catch(() => {});
        return prev;
      }
      pendingPlay.current = true;
      return track;
    });
  }, []);

  const playQueue = useCallback((tracks: PlayerTrack[], startIndex = 0, opts?: { shuffle?: boolean }) => {
    if (tracks.length === 0) return;
    clearPreview();
    const start = Math.max(0, Math.min(startIndex, tracks.length - 1));
    const useShuffle = opts?.shuffle ?? nav.current.shuffle;
    let ord: number[];
    let startPos: number;
    if (useShuffle) {
      const rest = shuffled(tracks.map((_, i) => i).filter((i) => i !== start));
      ord = [start, ...rest];
      startPos = 0;
    } else {
      ord = tracks.map((_, i) => i);
      startPos = start;
    }
    setQueue(tracks);
    setOrder(ord);
    setPos(startPos);
    if (opts?.shuffle !== undefined) setShuffle(useShuffle);
    pendingPlay.current = true;
    setCurrent(tracks[ord[startPos]]);
  }, []);

  const preview = useCallback((track: PlayerTrack) => {
    const a = audioRef.current;
    if (!a) return;
    if (current?.path === track.path) { a.play().catch(() => {}); return; }  // same track → control in place (dec 5)
    if (!current) { play(track); return; }                                    // nothing playing → normal play, no duck
    // Save the queue track only on the first duck; a nested preview keeps it (dec 4).
    if (!previewSaved.current) {
      previewSaved.current = { track: current, time: a.currentTime, wasPlaying: playing };
    }
    setPreviewing(true);
    rampVolume(a.volume, 0, FADE_MS, () => {
      pendingPlay.current = true;
      fadeIn.current = true;
      setCurrent(track);
    });
  }, [current, playing, play, rampVolume]);

  const toggleShuffle = useCallback(() => {
    const { queue, order, pos } = nav.current;
    const ns = !nav.current.shuffle;
    if (queue.length > 0) {
      const currentIdx = order[pos];
      if (ns) {
        const rest = shuffled(queue.map((_, i) => i).filter((i) => i !== currentIdx));
        setOrder(currentIdx != null ? [currentIdx, ...rest] : rest);
        setPos(0);
      } else {
        setOrder(queue.map((_, i) => i));
        setPos(currentIdx ?? 0);
      }
    }
    setShuffle(ns);
  }, []);

  const cycleRepeat = useCallback(() => {
    setRepeat((r) => (r === "off" ? "all" : r === "all" ? "one" : "off"));
  }, []);

  const toggle = useCallback(() => {
    const a = audioRef.current;
    if (!a) return;
    if (a.paused) a.play().catch(() => {});
    else a.pause();
  }, []);

  const stop = useCallback(() => {
    audioRef.current?.pause();
    clearPreview();
    setCurrent(null);
    setPlaying(false);
    setQueue([]);
    setOrder([]);
    setPos(-1);
  }, []);

  const jumpTo = useCallback((orderPos: number) => {
    clearPreview();
    const { order } = nav.current;
    if (orderPos >= 0 && orderPos < order.length) goToPos(orderPos);
  }, [goToPos]);

  const removeAt = useCallback((orderPos: number) => {
    const { order, pos, queue } = nav.current;
    if (orderPos < 0 || orderPos >= order.length) return;
    const newOrder = order.filter((_, i) => i !== orderPos);
    if (newOrder.length === 0) { stop(); return; }
    if (orderPos < pos) {
      setOrder(newOrder); setPos(pos - 1);
    } else if (orderPos > pos) {
      setOrder(newOrder);
    } else {
      // removed the current track → play whatever now sits at this slot (or last)
      const newPos = Math.min(pos, newOrder.length - 1);
      setOrder(newOrder); setPos(newPos);
      pendingPlay.current = true;
      setCurrent(queue[newOrder[newPos]]);
    }
  }, [stop]);

  const moveAt = useCallback((orderPos: number, dir: -1 | 1) => {
    const { order, pos } = nav.current;
    const j = orderPos + dir;
    if (orderPos < 0 || orderPos >= order.length || j < 0 || j >= order.length) return;
    const newOrder = order.slice();
    [newOrder[orderPos], newOrder[j]] = [newOrder[j], newOrder[orderPos]];
    setOrder(newOrder);
    if (pos === orderPos) setPos(j);
    else if (pos === j) setPos(orderPos);
  }, []);

  const enqueue = useCallback((track: PlayerTrack) => {
    const { queue, order } = nav.current;
    if (order.length === 0) { play(track); return; }  // nothing playing → start it
    const idx = queue.length;
    setQueue([...queue, track]);
    setOrder([...order, idx]);
  }, [play]);

  const playNext = useCallback((track: PlayerTrack) => {
    const { queue, order, pos } = nav.current;
    if (order.length === 0) { play(track); return; }
    const idx = queue.length;
    const newOrder = order.slice();
    newOrder.splice(pos + 1, 0, idx);
    setQueue([...queue, track]);
    setOrder(newOrder);
  }, [play]);

  const isCurrent = useCallback((p: string) => current?.path === p, [current]);

  // Global keyboard shortcuts (ignored while typing; Space is left for tap-tempo).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (!audioRef.current || !current) return;
      switch (e.key) {
        case "k": e.preventDefault(); toggle(); break;
        case "ArrowRight": next(false); break;
        case "ArrowLeft": prev(); break;
        case "+": case "=": setVolume(Math.min(1, volumeRef.current + 0.1)); break;
        case "-": setVolume(Math.max(0, volumeRef.current - 0.1)); break;
        case "m":
          if (volumeRef.current > 0) { mutePrev.current = volumeRef.current; setVolume(0); }
          else setVolume(mutePrev.current || 1);
          break;
        default: return;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [current, toggle, prev, next, setVolume]);

  const queueIndex = pos >= 0 && pos < order.length ? order[pos] : -1;
  const orderedQueue = order.map((i) => queue[i]).filter(Boolean);

  return (
    <Ctx.Provider value={{
      current, playing, error, audioRef,
      queue, queueIndex, orderedQueue, orderPos: pos,
      hasQueue: order.length > 1, shuffle, repeat, previewing, volume, setVolume,
      tempoLock, setTempoLock,
      play, playQueue, enqueue, playNext, preview, endPreview,
      next: () => next(false), prev, jumpTo, removeAt, moveAt, toggleShuffle, cycleRepeat,
      toggle, stop, isCurrent,
    }}>
      {children}
      <audio ref={audioRef} preload="auto" />
    </Ctx.Provider>
  );
}

export function usePlayer() {
  const c = useContext(Ctx);
  if (!c) throw new Error("usePlayer must be used within PlayerProvider");
  return c;
}
