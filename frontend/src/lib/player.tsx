import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { audioUrl } from "./api";

export interface PlayerTrack {
  path: string;
  title: string;
  artist?: string;
}

interface PlayerState {
  current: PlayerTrack | null;
  playing: boolean;
  audioRef: RefObject<HTMLAudioElement>;
  play(track: PlayerTrack): void;
  toggle(): void;
  stop(): void;
  isCurrent(path: string): boolean;
}

const Ctx = createContext<PlayerState | null>(null);

/** One <audio> element for the whole app, so playback survives route changes. */
export function PlayerProvider({ children }: { children: ReactNode }) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [current, setCurrent] = useState<PlayerTrack | null>(null);
  const [playing, setPlaying] = useState(false);
  const pendingPlay = useRef(false);

  // Load the source whenever the current track changes; auto-play if requested.
  useEffect(() => {
    const a = audioRef.current;
    if (!a || !current) return;
    a.src = audioUrl(current.path);
    a.load();
    if (pendingPlay.current) {
      a.play().catch(() => {});
      pendingPlay.current = false;
    }
  }, [current?.path]);

  // Reserve space at the bottom of the page for the bar while a track is loaded.
  useEffect(() => {
    document.body.classList.toggle("has-player", !!current);
  }, [current]);

  useEffect(() => {
    const a = audioRef.current;
    if (!a) return;
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    const onEnded = () => setPlaying(false);
    a.addEventListener("play", onPlay);
    a.addEventListener("pause", onPause);
    a.addEventListener("ended", onEnded);
    return () => {
      a.removeEventListener("play", onPlay);
      a.removeEventListener("pause", onPause);
      a.removeEventListener("ended", onEnded);
    };
  }, []);

  const play = useCallback((track: PlayerTrack) => {
    setCurrent((prev) => {
      if (prev?.path === track.path) {
        audioRef.current?.play().catch(() => {});
        return prev;
      }
      pendingPlay.current = true;
      return track;
    });
  }, []);

  const toggle = useCallback(() => {
    const a = audioRef.current;
    if (!a) return;
    if (a.paused) a.play().catch(() => {});
    else a.pause();
  }, []);

  const stop = useCallback(() => {
    audioRef.current?.pause();
    setCurrent(null);
    setPlaying(false);
  }, []);

  const isCurrent = useCallback((p: string) => current?.path === p, [current]);

  return (
    <Ctx.Provider value={{ current, playing, audioRef, play, toggle, stop, isCurrent }}>
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
