import { useEffect, useState, type RefObject } from "react";

/** Track currentTime/duration of an <audio> element via its events (local state
 *  so it doesn't re-render the whole app on every timeupdate). */
export function useAudioTime(audioRef: RefObject<HTMLAudioElement>) {
  const [time, setTime] = useState(0);
  const [dur, setDur] = useState(0);
  useEffect(() => {
    const a = audioRef.current;
    if (!a) return;
    const onTime = () => setTime(a.currentTime);
    const onMeta = () => setDur(a.duration || 0);
    a.addEventListener("timeupdate", onTime);
    a.addEventListener("loadedmetadata", onMeta);
    a.addEventListener("durationchange", onMeta);
    setTime(a.currentTime);
    setDur(a.duration || 0);
    return () => {
      a.removeEventListener("timeupdate", onTime);
      a.removeEventListener("loadedmetadata", onMeta);
      a.removeEventListener("durationchange", onMeta);
    };
  }, [audioRef]);
  return { time, dur };
}

export function fmtTime(s: number): string {
  if (!isFinite(s) || s < 0) return "0:00";
  const m = Math.floor(s / 60);
  return m + ":" + String(Math.floor(s % 60)).padStart(2, "0");
}
