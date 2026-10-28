import { useRef, type RefObject } from "react";
import { useAudioTime } from "./useAudioTime";
import { useCoverGlow } from "./useCoverGlow";
import { useWaveform } from "./useWaveform";

/** The playback plumbing every full-screen player page needs: the audio element's
 *  time and duration, a waveform canvas wired to it, and the ambient cover glow.
 *
 *  Run and Listen each wired these three by hand, identically; this is that
 *  wiring in one place. `waveform` says whether there is a real track to draw
 *  (a one-off preview clip has none), `glowPath` is the cover to tint from, or
 *  null for no glow. */
export function usePlaybackSurface(
  audioRef: RefObject<HTMLAudioElement>,
  { path, waveform, glowPath }: { path: string; waveform: boolean; glowPath: string | null },
) {
  const { time, dur } = useAudioTime(audioRef);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  useWaveform(canvasRef, audioRef, path, waveform, waveform);
  const glow = useCoverGlow(glowPath);
  return { time, dur, canvasRef, glow };
}
