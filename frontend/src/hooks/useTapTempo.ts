import { useCallback, useEffect, useRef, useState } from "react";

// Tap tempo: BPM = 60000 / mean of the last ≤8 inter-tap intervals.
// A gap ≥3000ms resets the sample set; 3s of silence auto-resets everything.
// Ported verbatim from the track.html tap-tempo logic.
//
// `enabled` (default true) gates the global Space-to-tap listener. The Run page
// passes false while the tempo lock is on — tapping there only makes sense at
// the track's true speed, so it's disabled until the lock is released.
export function useTapTempo(enabled = true) {
  const [display, setDisplay] = useState<string>("—"); // "—" or a fixed(1) BPM string
  const [taps, setTaps] = useState(0);
  const intervals = useRef<number[]>([]);
  const lastTap = useRef<number | null>(null);
  const timeout = useRef<number | undefined>(undefined);

  const reset = useCallback(() => {
    intervals.current = [];
    lastTap.current = null;
    if (timeout.current) {
      window.clearTimeout(timeout.current);
      timeout.current = undefined;
    }
    setDisplay("—");
    setTaps(0);
  }, []);

  const onTap = useCallback(() => {
    const now = Date.now();
    if (timeout.current) window.clearTimeout(timeout.current);
    timeout.current = window.setTimeout(reset, 3000);

    if (lastTap.current !== null) {
      const gap = now - lastTap.current;
      if (gap < 3000) {
        if (intervals.current.length >= 8) intervals.current.shift();
        intervals.current.push(gap);
        const avg = intervals.current.reduce((a, b) => a + b, 0) / intervals.current.length;
        setDisplay((60000 / avg).toFixed(1));
        setTaps(intervals.current.length);
      } else {
        intervals.current = [];
        setDisplay("—");
        setTaps(0);
      }
    }
    lastTap.current = now;
  }, [reset]);

  // Space anywhere (except when focused in an input/button/textarea) triggers a tap.
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as Element | null;
      if (e.code === "Space" && target && !target.matches("input, button, textarea")) {
        e.preventDefault();
        onTap();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onTap, enabled]);

  // Clear any pending auto-reset timer on unmount.
  useEffect(() => () => {
    if (timeout.current) window.clearTimeout(timeout.current);
  }, []);

  return { display, taps, canApply: display !== "—", onTap, reset };
}
