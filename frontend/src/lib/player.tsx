import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { api, audioUrl, notifyUnauthorized } from "./api";

export interface PlayerTrack {
  path: string;            // identity key; for previews a synthetic "preview:dz:<id>"
  title: string;
  artist?: string;
  bpm?: number | null;
  src?: string;            // absolute stream URL; used instead of audioUrl(path) when set
  ephemeral?: boolean;     // one-off external clip — never persisted (dies on reload)
  fromPlaylist?: boolean;  // run mode: from the selected playlist vs a library top-up
  starred?: boolean;       // run mode: last-known star state (for the queue's star toggle)
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
// How many of the most-recently-queued tracks the auto-refill tells the server
// to avoid re-picking. Bounded rather than the whole run's history: only
// near-term repeats matter, and it keeps the request small on a long run.
const REFILL_EXCLUDE_WINDOW = 60;

interface PlayerState {
  current: PlayerTrack | null;
  playing: boolean;
  error: string | null;
  buffering: boolean;      // playback stalled waiting for data (slow/dropped link)
  bufferedPct: number;     // % of the current track buffered ahead (0–100)
  online: boolean;         // navigator.onLine — false when the connection dropped
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
  // The run's source scope: a playlist id, or null for the whole library. Set
  // by the Run page on Start so the mid-run auto-refill stays inside the chosen
  // source instead of drifting back to the whole library.
  runSource: number | null;
  setRunSource(id: number | null): void;
  /** Refresh a queued track's BPM (e.g. after fixing it on the track page) so
   *  a live tempo lock re-stretches immediately instead of waiting for a rebuild. */
  updateTrackBpm(path: string, bpm: number | null): void;
  /** Optimistically reflect a star toggle on the matching queued track — used by
   *  the Run queue so refilled tracks (never in the page's build response) update
   *  too. */
  setTrackStarred(path: string, starred: boolean): void;
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
  runSource?: number | null;
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
  // Whether the queue *should* be playing, as distinct from `playing` (whether
  // the <audio> element literally is right now). They diverge during a track
  // transition: iOS can veto the auto-advance play() call while the app is
  // backgrounded, leaving `playing` false — sometimes for as long as the phone
  // stays locked — even though nothing about user intent changed. Reporting
  // that gap to the OS as MediaSession "paused" tells iOS this session is
  // inactive, and it can hand the lock-screen Now Playing widget to a
  // different app instead. `intendedPlaying` only flips false on a genuine
  // stop (explicit pause, or the queue truly running out) — see the
  // MediaSession playbackState effect below.
  const [intendedPlaying, setIntendedPlaying] = useState(() => !!(current && saved?.playing));
  const [error, setError] = useState<string | null>(null);
  // Slow-connection visibility: `buffering` while the element is waiting for
  // data, `bufferedPct` how much of the current track has loaded, `online` from
  // the browser's network status — so a stall reads as the network, not the app.
  const [buffering, setBuffering] = useState(false);
  const [bufferedPct, setBufferedPct] = useState(0);
  const [online, setOnline] = useState(() => typeof navigator === "undefined" || navigator.onLine !== false);
  // Run-mode look-ahead: silent <audio> elements that pre-buffer the next tracks.
  const preRefs = useRef<(HTMLAudioElement | null)[]>([null, null]);
  const pendingPlay = useRef(!!(current && saved?.playing));
  // Path already loaded + started synchronously by goToPos, so the load effect
  // must not reload it (that would restart the fetch and kill iOS lock-screen
  // playback — see goToPos).
  const syncedPath = useRef<string | null>(null);
  // An auto-advance play() was vetoed (iOS suspended/backgrounded the page at a
  // track boundary) — resume as soon as the app is visible again.
  const resumeOnShow = useRef(false);
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
  // Run source scope (playlist id | null). Mirrored to a ref so the auto-refill
  // effect can read it without adding a dependency that would re-fire it.
  const [runSource, setRunSourceState] = useState<number | null>(() => saved?.runSource ?? null);
  const runSourceRef = useRef(runSource);
  const setRunSource = useCallback((id: number | null) => {
    runSourceRef.current = id;
    setRunSourceState(id);
  }, []);
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
      // Ephemeral tracks are one-off external preview clips whose URLs die on
      // reload; they only ever reach the queue via the "preview with nothing
      // playing" fallthrough (a single-item queue). Never persist them — filter
      // them out, and if that leaves nothing, clear the saved queue entirely.
      if (queue.some((t) => t.ephemeral)) {
        const survivors = queue.filter((t) => !t.ephemeral);
        if (!survivors.length) { localStorage.removeItem(SAVE_KEY); return; }
        localStorage.setItem(SAVE_KEY, JSON.stringify({
          queue: survivors, order: survivors.map((_, i) => i), pos: 0,
          shuffle, repeat, volume: volumeRef.current, time: 0, playing: false,
          tempoLock: nav.current.tempoLock, runSource: runSourceRef.current,
        }));
        return;
      }
      const a = audioRef.current;
      const pv = previewSaved.current;
      // While a preview is ducking the queue, save the queue track's saved
      // position/state — not the preview's.
      const time = pv ? pv.time : a?.currentTime || 0;
      const isPlaying = pv ? pv.wasPlaying : !!a && !a.paused;
      localStorage.setItem(SAVE_KEY, JSON.stringify({
        queue, order, pos, shuffle, repeat,
        volume: volumeRef.current, time, playing: isPlaying,
        tempoLock: nav.current.tempoLock, runSource: runSourceRef.current,
      }));
    } catch { /* ignore */ }
  }, []);
  useEffect(persist, [queue, order, pos, shuffle, repeat, volume, tempoLock, runSource, persist]);

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
    const clamp = (v: number) => Math.max(0, Math.min(1, v));
    a.volume = clamp(from);
    const step = (now: number) => {
      // The rAF timestamp can land marginally before `start`, making the raw
      // fraction slightly negative — clamp to [0,1] so a fade-in never writes a
      // negative volume (HTMLMediaElement.volume throws outside [0,1]).
      const t = Math.max(0, Math.min(1, (now - start) / ms));
      a.volume = clamp(from + (to - from) * t);
      if (t < 1) rampRef.current = requestAnimationFrame(step);
    };
    rampRef.current = requestAnimationFrame(step);
    rampTimer.current = window.setTimeout(() => {
      if (rampRef.current != null) { cancelAnimationFrame(rampRef.current); rampRef.current = null; }
      a.volume = clamp(to);
      done?.();
    }, ms);
  }, []);

  // Play, recovering from a failed source first. A stream error (expired
  // session, network drop) leaves the element in a dead error state where
  // play() rejects forever — load() the same URL so a retry re-fetches it.
  const resumePlay = useCallback(() => {
    const a = audioRef.current;
    if (!a) return;
    setIntendedPlaying(true);
    if (a.error) {
      setError(null);
      a.load();
    }
    a.play().catch(() => {});
  }, []);

  // Safety net for the iOS PWA: if an auto-advance play() was vetoed while the
  // page was suspended, restart playback the moment the app is shown again so
  // the run doesn't stay silent after unlocking the phone.
  useEffect(() => {
    const onShow = () => {
      if (document.visibilityState !== "visible" || !resumeOnShow.current) return;
      resumeOnShow.current = false;
      resumePlay();
    };
    document.addEventListener("visibilitychange", onShow);
    window.addEventListener("pageshow", onShow);
    return () => {
      document.removeEventListener("visibilitychange", onShow);
      window.removeEventListener("pageshow", onShow);
    };
  }, [resumePlay]);

  // Load the source whenever the current track changes; seek + fade-in + auto-play as requested.
  useEffect(() => {
    const a = audioRef.current;
    if (!a || !current) return;
    // goToPos already loaded + started this track synchronously — reloading
    // here would restart the fetch and cut iOS lock-screen playback.
    const alreadyStarted = syncedPath.current === current.path;
    syncedPath.current = null;
    if (alreadyStarted) { pendingPlay.current = false; return; }
    setError(null);
    a.src = current.src ?? audioUrl(current.path);
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

  // Run mode: when the LAST queued track starts, fetch a fresh batch for the
  // same target and append it, so a run keeps going instead of stopping at the
  // queue end. Lives here (not in the Run page) so it works with the app on any
  // route or the phone locked. Skipped when repeat already loops the queue.
  // The recently-queued paths are sent as `exclude` so the refill surfaces
  // unplayed matches first; the server only reshuffles from the full pool
  // (marking the response `recycled`) once every non-excluded match is gone —
  // e.g. a small library on a long run — so the queue never dries up.
  const extending = useRef(false);
  useEffect(() => {
    if (!tempoLock || previewing || repeat !== "off") return;
    if (order.length === 0 || pos !== order.length - 1) return;
    if (extending.current) return;
    extending.current = true;
    const exclude = nav.current.queue.slice(-REFILL_EXCLUDE_WINDOW).map((t) => t.path);
    // Keep the refill inside the run's source: pass the playlist scope so a
    // playlist run reshuffles its own tracks (server's `recycled` fallback) once
    // the unplayed matches run out, instead of pulling from the whole library.
    const body: { bpm: number; exclude: string[]; playlist?: number } = { bpm: tempoLock.target, exclude };
    if (runSourceRef.current != null) body.playlist = runSourceRef.current;
    api.post<{ tracks: { path: string; title: string; artist?: string; bpm: number; starred?: boolean; from_playlist?: boolean }[] }>(
      "/api/run/queue", body)
      .then((resp) => {
        const { queue, order, pos } = nav.current;
        const cur = queue[order[pos]];
        let batch: PlayerTrack[] = resp.tracks.map((t) =>
          ({ path: t.path, title: t.title, artist: t.artist, bpm: t.bpm, starred: t.starred, fromPlaylist: t.from_playlist }));
        // Defense in depth: the exclude window is bounded, so the currently
        // playing track could in principle fall outside it on a huge queue.
        const noRepeat = batch.filter((t) => t.path !== cur?.path);
        if (noRepeat.length) batch = noRepeat;
        if (!batch.length) return;
        const base = queue.length;
        setQueue([...queue, ...batch]);
        setOrder([...order, ...batch.map((_, i) => base + i)]);
      })
      .catch(() => {})  // a failed refill just means the run ends at the queue end
      .finally(() => { extending.current = false; });
  }, [tempoLock, previewing, repeat, pos, order.length]);

  const cancelRamp = () => {
    if (rampRef.current != null) { cancelAnimationFrame(rampRef.current); rampRef.current = null; }
    if (rampTimer.current != null) { clearTimeout(rampTimer.current); rampTimer.current = null; }
  };
  // Called by every take-over path (play/playQueue/next/prev/stop) — also cancels
  // any in-flight fade so a pending swap can't clobber the new track.
  const clearPreview = () => { cancelRamp(); previewSaved.current = null; setPreviewing(false); };

  // Queue jumps swap the source and call play() synchronously on the element,
  // then sync React state. Deferring the swap to the load effect breaks iOS
  // lock-screen playback: the WebView is only kept alive while audio plays, so
  // after `ended` fires the page can be suspended before React flushes the
  // effect — play() never runs and the queue dies mid-run. Starting the next
  // track inside the same call stack as the media/lock-screen event keeps the
  // audio session alive.
  const goToPos = useCallback((newPos: number) => {
    const { queue, order, tempoLock } = nav.current;
    const track = queue[order[newPos]];
    if (!track) return;
    const a = audioRef.current;
    setIntendedPlaying(true);
    if (a) {
      setError(null);
      const rate = lockRate(track.bpm, tempoLock);
      a.defaultPlaybackRate = rate;   // the load triggered by src= resets playbackRate to this
      a.src = track.src ?? audioUrl(track.path);
      a.playbackRate = rate;
      a.volume = volumeRef.current;
      a.play().catch(() => {
        // Vetoed (page suspended at the boundary) — retry when visible again.
        // Skip if the queue has already moved on (e.g. rapid next presses).
        const { queue, order, pos } = nav.current;
        if (queue[order[pos]]?.path === track.path) resumeOnShow.current = true;
      });
      syncedPath.current = track.path;
    } else {
      pendingPlay.current = true;
    }
    setPos(newPos);
    setCurrent(track);
  }, []);

  const next = useCallback((auto = false) => {
    clearPreview();                       // explicit queue action takes over (dec 6)
    const a = audioRef.current;
    const { order, pos, repeat } = nav.current;
    if (auto && repeat === "one") {
      if (a) { a.currentTime = 0; a.play().catch(() => {}); }
      setIntendedPlaying(true);
      return;
    }
    if (order.length === 0) { setIntendedPlaying(false); return; }
    let np = pos + 1;
    if (np >= order.length) {
      if (repeat === "all") np = 0;
      else { setIntendedPlaying(false); return; }               // end of queue, no repeat → stop advancing
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
      setIntendedPlaying(saved.wasPlaying);
      setCurrent(saved.track);
    };
    const a = audioRef.current;
    if (a && !a.paused) rampVolume(a.volume, 0, FADE_MS, restore);
    else restore();
  }, [rampVolume]);

  // Scrobble: report a library track as played once it passes the halfway mark
  // (Last.fm convention). The server forwards to Navidrome when scrobbling is
  // enabled there and no-ops otherwise, so this always fires and forgets.
  // Seeking back under 5s re-arms the same track (a genuine replay); previews
  // and external clips (src set / ephemeral) never scrobble.
  const currentRef = useRef(current);
  useEffect(() => { currentRef.current = current; }, [current]);
  const scrobbledPath = useRef<string | null>(null);
  useEffect(() => { scrobbledPath.current = null; }, [current?.path]);
  useEffect(() => {
    const a = audioRef.current;
    if (!a) return;
    const onTime = () => {
      const c = currentRef.current;
      if (!c || c.ephemeral || c.src) return;
      const d = a.duration;
      if (!isFinite(d) || d < 30) return;   // skip blips and unknown durations
      if (a.currentTime < 5 && scrobbledPath.current === c.path) scrobbledPath.current = null;
      if (a.currentTime / d >= 0.5 && scrobbledPath.current !== c.path) {
        scrobbledPath.current = c.path;
        api.post("/api/scrobble", { path: c.path }).catch(() => {});
      }
    };
    a.addEventListener("timeupdate", onTime);
    return () => a.removeEventListener("timeupdate", onTime);
  }, []);

  // ── Run-mode usage stats ────────────────────────────────────────────────
  // While a tempo-locked run plays a library track, accumulate listening time
  // (split into tempo-shifted vs native), native audio duration covered, a
  // time-weighted cadence sum, per-cadence-bin buckets, and a track count.
  // Deltas are batched and POSTed to /api/run/stat (every 20s + on pause /
  // track change / page hide); the server keeps cumulative totals for Stats.
  //
  // Timing is derived from the <audio> element's currentTime, not a wall clock:
  // currentTime reflects audio that actually played even when JS was throttled
  // (locked phone, backgrounded PWA — the primary run case), so a big gap
  // between samples is still counted. Samples with a negative delta (seek back)
  // or an implausibly large one (seek forward / re-baseline) are skipped.
  const RUN_COUNT_MS = 30_000;      // a track counts once it holds ~30s of run play
  const RUN_FLUSH_MS = 20_000;
  const runAcc = useRef({ wall: 0, shifted: 0, native: 0, cadence: 0, tracks: 0, bands: {} as Record<string, number> });
  const runPrevTime = useRef<number | null>(null);   // last sampled currentTime (s)
  const runTrackWall = useRef(0);                     // wall ms held by the current track
  const runCountedPath = useRef<string | null>(null); // path already counted in `tracks`

  const sampleRun = useCallback(() => {
    const a = audioRef.current;
    const c = currentRef.current;
    const lock = nav.current.tempoLock;
    // Only real library tracks under an active tempo lock, while actually playing.
    if (!a || !c || !lock || c.ephemeral || c.src || a.paused) { runPrevTime.current = null; return; }
    const t = a.currentTime;
    const prev = runPrevTime.current;
    runPrevTime.current = t;
    if (prev == null) return;                          // first sample → baseline only
    const dNative = t - prev;                          // source seconds advanced
    if (!(dNative > 0) || dNative > 90) return;         // seek / big gap → re-baseline
    const rate = a.playbackRate || 1;
    const wall = (dNative / rate) * 1000;              // real ms on feet
    const acc = runAcc.current;
    acc.wall += wall;
    acc.native += dNative * 1000;                      // native audio ms consumed
    if (Math.abs(rate - 1) > 0.01) acc.shifted += wall;
    acc.cadence += lock.target * wall;                 // time-weighted cadence
    const bin = Math.floor(lock.target / 10) * 10;      // 10-BPM cadence bucket
    const key = `cad_${bin}`;
    acc.bands[key] = (acc.bands[key] || 0) + wall;
    runTrackWall.current += wall;
    if (runCountedPath.current !== c.path && runTrackWall.current >= RUN_COUNT_MS) {
      runCountedPath.current = c.path;
      acc.tracks += 1;
    }
  }, []);

  const flushRun = useCallback(() => {
    const acc = runAcc.current;
    const deltas: Record<string, number> = {};
    if (acc.wall) deltas.wall_ms = Math.round(acc.wall);
    if (acc.shifted) deltas.shifted_ms = Math.round(acc.shifted);
    if (acc.native) deltas.native_ms = Math.round(acc.native);
    if (acc.cadence) deltas.cadence_weighted = Math.round(acc.cadence);
    if (acc.tracks) deltas.tracks_played = acc.tracks;
    for (const k in acc.bands) { const v = Math.round(acc.bands[k]); if (v) deltas[k] = v; }
    if (Object.keys(deltas).length === 0) return;
    runAcc.current = { wall: 0, shifted: 0, native: 0, cadence: 0, tracks: 0, bands: {} };
    api.post("/api/run/stat", { deltas }).catch(() => {});
  }, []);

  // Re-baseline per track so a track boundary never counts as one big delta.
  useEffect(() => { runPrevTime.current = null; runTrackWall.current = 0; }, [current?.path]);

  // Sample on timeupdate; flush on a timer and when the page is hidden.
  useEffect(() => {
    const a = audioRef.current;
    if (!a) return;
    const onTime = () => sampleRun();
    const onHide = () => { if (document.visibilityState === "hidden") { sampleRun(); flushRun(); } };
    a.addEventListener("timeupdate", onTime);
    const flushId = window.setInterval(flushRun, RUN_FLUSH_MS);
    document.addEventListener("visibilitychange", onHide);
    window.addEventListener("pagehide", flushRun);
    return () => {
      a.removeEventListener("timeupdate", onTime);
      clearInterval(flushId);
      document.removeEventListener("visibilitychange", onHide);
      window.removeEventListener("pagehide", flushRun);
    };
  }, [sampleRun, flushRun]);

  // On pause / stop / queue end, capture the last delta and flush.
  useEffect(() => { if (!playing) { sampleRun(); flushRun(); } }, [playing, sampleRun, flushRun]);

  // Audio element event wiring (attached once).
  useEffect(() => {
    const a = audioRef.current;
    if (!a) return;
    const onPlay = () => { setPlaying(true); setIntendedPlaying(true); setError(null); resumeOnShow.current = false; };
    const onPause = () => setPlaying(false);
    // Preview ended → resume the queue (dec 2); otherwise auto-advance.
    const onEnded = () => { setPlaying(false); if (previewSaved.current) endPreview(); else next(true); };
    // A failed stream (404, decode error, network drop) would otherwise leave
    // the UI stuck in a "playing" state with no feedback — surface it instead.
    // The saved queue track is kept so leaving still restores it (dec 9).
    // The media element only ever reports SRC_NOT_SUPPORTED, so probe the same
    // URL to tell an expired session (common in the installed PWA) apart from a
    // genuinely missing/unsupported file — a 401 routes to the login screen.
    const onError = () => {
      setPlaying(false);
      const src = a.currentSrc || a.src;
      if (!src) { setError("Playback failed — the file may be missing or unsupported."); return; }
      fetch(src, { method: "HEAD", credentials: "same-origin" })
        .then((r) => {
          // Ignore a stale probe: the element recovered or moved to another track.
          if (!a.error || (a.currentSrc || a.src) !== src) return;
          if (r.status === 401) {
            setError("Session expired — sign in, then press play to resume.");
            notifyUnauthorized();
          } else {
            setError("Playback failed — the file may be missing or unsupported.");
          }
        })
        .catch(() => {
          if (a.error) setError("Playback failed — check your connection and press play to retry.");
        });
    };
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

  // Buffering + buffered-ahead % (slow-connection visibility). Attached once;
  // reads the element directly. `timeupdate` doubles as a "we're advancing"
  // signal that clears a stale buffering flag (it can't fire while truly stalled).
  useEffect(() => {
    const a = audioRef.current;
    if (!a) return;
    const recalc = () => {
      const d = a.duration;
      if (!isFinite(d) || d <= 0) { setBufferedPct(0); return; }
      let end = 0;
      try {
        const b = a.buffered;
        for (let i = 0; i < b.length; i++) {
          if (b.start(i) <= a.currentTime + 0.25 && b.end(i) > end) end = b.end(i);
        }
        if (!end && b.length) end = b.end(b.length - 1);
      } catch { /* ignore */ }
      setBufferedPct(Math.max(0, Math.min(100, Math.round((end / d) * 100))));
    };
    const onWaiting = () => setBuffering(true);
    const onStalled = () => setBuffering(true);
    const clear = () => { setBuffering(false); recalc(); };
    a.addEventListener("waiting", onWaiting);
    a.addEventListener("stalled", onStalled);
    a.addEventListener("playing", clear);
    a.addEventListener("canplay", clear);
    a.addEventListener("canplaythrough", clear);
    a.addEventListener("progress", recalc);
    a.addEventListener("timeupdate", clear);
    return () => {
      a.removeEventListener("waiting", onWaiting);
      a.removeEventListener("stalled", onStalled);
      a.removeEventListener("playing", clear);
      a.removeEventListener("canplay", clear);
      a.removeEventListener("canplaythrough", clear);
      a.removeEventListener("progress", recalc);
      a.removeEventListener("timeupdate", clear);
    };
  }, []);

  // Reset the buffering readout when the track changes (new load starts fresh).
  useEffect(() => { setBuffering(false); setBufferedPct(0); }, [current?.path]);

  // Browser online/offline — a dropped connection is shown as such, not as an
  // app fault. (navigator.onLine is coarse but enough to distinguish the case.)
  useEffect(() => {
    const on = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
    };
  }, []);

  // Run-mode look-ahead: while a tempo-locked run is playing (a predictable,
  // long queue), pre-buffer the next couple of tracks in silent <audio>
  // elements so a track boundary doesn't stall on a slow link. Gated to run
  // mode so we never spend bandwidth prefetching outside it; never played.
  const PRELOAD_AHEAD = 2;
  useEffect(() => {
    const slots = preRefs.current;
    const clearSlot = (s: HTMLAudioElement | null) => {
      if (s && s.getAttribute("src")) { s.removeAttribute("src"); try { s.load(); } catch { /* ignore */ } }
    };
    if (tempoLock == null || !playing || order.length <= 1) { slots.forEach(clearSlot); return; }
    const wants: string[] = [];
    for (let k = 1; k <= PRELOAD_AHEAD; k++) {
      let np = pos + k;
      if (np >= order.length) { if (repeat === "all") np %= order.length; else break; }
      const t = queue[order[np]];
      if (t && !t.ephemeral && !t.src) wants.push(audioUrl(t.path));
    }
    slots.forEach((s, i) => {
      if (!s) return;
      const want = wants[i] || "";
      if (want) { if (s.getAttribute("src") !== want) { s.src = want; try { s.load(); } catch { /* ignore */ } } }
      else clearSlot(s);
    });
  }, [current?.path, playing, order, pos, repeat, tempoLock, queue]);

  // Media Session: lock-screen / headset / notification controls (key for the
  // PWA running use case — the phone is locked mid-run). Metadata mirrors the
  // current track; cover art 404s are fine (the OS just shows no artwork).
  useEffect(() => {
    if (!("mediaSession" in navigator)) return;
    navigator.mediaSession.metadata = current
      ? new MediaMetadata({
          title: current.title,
          artist: current.artist ?? "",
          // Ephemeral preview clips have a synthetic path with no library file,
          // so skip the cover fetch (it would 403) rather than 404-tolerate it.
          artwork: current.ephemeral
            ? []
            : [{ src: `/api/track/cover?path=${encodeURIComponent(current.path)}`, sizes: "512x512" }],
        })
      : null;
  }, [current]);

  useEffect(() => {
    if (!("mediaSession" in navigator)) return;
    navigator.mediaSession.playbackState = current ? (intendedPlaying ? "playing" : "paused") : "none";
  }, [current, intendedPlaying]);

  useEffect(() => {
    if (!("mediaSession" in navigator)) return;
    const ms = navigator.mediaSession;
    ms.setActionHandler("play", resumePlay);
    ms.setActionHandler("pause", () => { setIntendedPlaying(false); audioRef.current?.pause(); });
    ms.setActionHandler("previoustrack", () => prev());
    ms.setActionHandler("nexttrack", () => next(false));
    return () => {
      for (const a of ["play", "pause", "previoustrack", "nexttrack"] as MediaSessionAction[]) {
        ms.setActionHandler(a, null);
      }
    };
  }, [prev, next, resumePlay]);

  const play = useCallback((track: PlayerTrack) => {
    // One-off play (e.g. a library/search row): becomes a single-item queue so
    // prev/next are inert and shuffle has no stale context. Takes over any preview.
    clearPreview();
    setQueue([track]);
    setOrder([0]);
    setPos(0);
    setIntendedPlaying(true);
    setCurrent((prev) => {
      if (prev?.path === track.path) {
        resumePlay();
        return prev;
      }
      pendingPlay.current = true;
      return track;
    });
  }, [resumePlay]);

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
    setIntendedPlaying(true);
    setCurrent(tracks[ord[startPos]]);
  }, []);

  const preview = useCallback((track: PlayerTrack) => {
    const a = audioRef.current;
    if (!a) return;
    if (current?.path === track.path) { resumePlay(); return; }  // same track → control in place (dec 5)
    if (!current) { play(track); return; }                                    // nothing playing → normal play, no duck
    // Save the queue track only on the first duck; a nested preview keeps it (dec 4).
    if (!previewSaved.current) {
      previewSaved.current = { track: current, time: a.currentTime, wasPlaying: playing };
    }
    setPreviewing(true);
    rampVolume(a.volume, 0, FADE_MS, () => {
      pendingPlay.current = true;
      fadeIn.current = true;
      setIntendedPlaying(true);
      setCurrent(track);
    });
  }, [current, playing, play, rampVolume, resumePlay]);

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
    // An errored element can report paused=false — treat it as resumable.
    if (a.error || a.paused) resumePlay();
    else { setIntendedPlaying(false); a.pause(); }
  }, [resumePlay]);

  const stop = useCallback(() => {
    audioRef.current?.pause();
    clearPreview();
    setCurrent(null);
    setPlaying(false);
    setIntendedPlaying(false);
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
      setIntendedPlaying(true);
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

  // New object, same path: the load effect keys on current.path so playback
  // doesn't restart, but the tempo-lock effect re-runs with the fresh BPM.
  const updateTrackBpm = useCallback((path: string, bpm: number | null) => {
    setQueue((q) => q.map((t) => (t.path === path ? { ...t, bpm } : t)));
    setCurrent((c) => (c && c.path === path && c.bpm !== bpm ? { ...c, bpm } : c));
  }, []);

  // Reflect a star toggle onto the queued track (queue rows read t.starred).
  const setTrackStarred = useCallback((path: string, starred: boolean) => {
    setQueue((q) => q.map((t) => (t.path === path && t.starred !== starred ? { ...t, starred } : t)));
  }, []);

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
      current, playing, error, buffering, bufferedPct, online, audioRef,
      queue, queueIndex, orderedQueue, orderPos: pos,
      hasQueue: order.length > 1, shuffle, repeat, previewing, volume, setVolume,
      tempoLock, setTempoLock, runSource, setRunSource, updateTrackBpm, setTrackStarred,
      play, playQueue, enqueue, playNext, preview, endPreview,
      next: () => next(false), prev, jumpTo, removeAt, moveAt, toggleShuffle, cycleRepeat,
      toggle, stop, isCurrent,
    }}>
      {children}
      <audio ref={audioRef} preload="auto" />
      {/* Run-mode look-ahead buffers — silent, never played (see PRELOAD_AHEAD). */}
      <audio ref={(el) => { preRefs.current[0] = el; }} preload="auto" muted aria-hidden="true" style={{ display: "none" }} />
      <audio ref={(el) => { preRefs.current[1] = el; }} preload="auto" muted aria-hidden="true" style={{ display: "none" }} />
    </Ctx.Provider>
  );
}

export function usePlayer() {
  const c = useContext(Ctx);
  if (!c) throw new Error("usePlayer must be used within PlayerProvider");
  return c;
}
