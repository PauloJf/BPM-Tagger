import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { audioUrl } from "./api";

export interface PlayerTrack {
  path: string;
  title: string;
  artist?: string;
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
  hasQueue: boolean;       // more than one track queued
  shuffle: boolean;
  repeat: RepeatMode;
  previewing: boolean;     // a detail/compare preview is ducking the main queue
  play(track: PlayerTrack): void;                                  // one-off
  playQueue(tracks: PlayerTrack[], startIndex?: number, opts?: { shuffle?: boolean }): void;
  preview(track: PlayerTrack): void;   // duck the queue, play track, resume on end/leave
  endPreview(): void;                  // fade back to the saved queue track
  next(): void;
  prev(): void;
  toggleShuffle(): void;
  cycleRepeat(): void;
  toggle(): void;
  stop(): void;
  isCurrent(path: string): boolean;
}

const Ctx = createContext<PlayerState | null>(null);

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
  const [current, setCurrent] = useState<PlayerTrack | null>(null);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pendingPlay = useRef(false);
  // Set before a setCurrent to seek the freshly-loaded track (used to restore a
  // preview's saved position) and to fade the next play in from silence.
  const seekTarget = useRef<number | null>(null);
  const fadeIn = useRef(false);

  // Queue state. `order` holds queue indices in playback order (so shuffle can
  // be toggled without losing the current track); `pos` is the position within
  // `order`. queueIndex = order[pos].
  const [queue, setQueue] = useState<PlayerTrack[]>([]);
  const [order, setOrder] = useState<number[]>([]);
  const [pos, setPos] = useState(-1);
  const [shuffle, setShuffle] = useState(false);
  const [repeat, setRepeat] = useState<RepeatMode>("off");

  // Ducking preview: while active, the queue track + position + play-state are
  // stashed here and restored when the preview ends or the user leaves.
  const previewSaved = useRef<{ track: PlayerTrack; time: number; wasPlaying: boolean } | null>(null);
  const [previewing, setPreviewing] = useState(false);

  // Mirror queue state into a ref so the (once-attached) "ended" handler and the
  // stable next/prev callbacks always read the latest values.
  const nav = useRef({ queue, order, pos, repeat, shuffle });
  useEffect(() => { nav.current = { queue, order, pos, repeat, shuffle }; }, [queue, order, pos, repeat, shuffle]);

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
        a.volume = doFadeIn ? 0 : 1;
        a.play().catch(() => {});
        if (doFadeIn) rampVolume(0, 1, FADE_MS);
      } else {
        a.volume = 1;
      }
    };
    if (seekTo != null) {
      a.addEventListener("loadedmetadata", begin, { once: true });
      return () => a.removeEventListener("loadedmetadata", begin);
    }
    begin();
  }, [current?.path, rampVolume]);

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

  const isCurrent = useCallback((p: string) => current?.path === p, [current]);

  const queueIndex = pos >= 0 && pos < order.length ? order[pos] : -1;

  return (
    <Ctx.Provider value={{
      current, playing, error, audioRef,
      queue, queueIndex, hasQueue: queue.length > 1, shuffle, repeat, previewing,
      play, playQueue, preview, endPreview,
      next: () => next(false), prev, toggleShuffle, cycleRepeat,
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
