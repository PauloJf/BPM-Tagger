import { useRef } from "react";
import { usePlayer } from "../lib/player";
import { fmtTime, useAudioTime } from "../hooks/useAudioTime";
import { useWaveform } from "../hooks/useWaveform";

export default function PlayerBar() {
  const { current, playing, audioRef, toggle, stop } = usePlayer();
  const { time, dur } = useAudioTime(audioRef);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  // Hooks must run unconditionally; disabled until a track is loaded.
  useWaveform(canvasRef, audioRef, current?.path || "", !!current, !!current);

  if (!current) return null;

  return (
    <div className="player-bar">
      <button className="player-bar-play" onClick={toggle} aria-label={playing ? "Pause" : "Play"}>
        {playing ? (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="white"><rect x="6" y="4" width="4" height="16" rx="1" /><rect x="14" y="4" width="4" height="16" rx="1" /></svg>
        ) : (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="white"><polygon points="6,4 20,12 6,20" /></svg>
        )}
      </button>
      <div className="player-bar-meta">
        <div className="player-bar-title">{current.title}</div>
        {current.artist ? <div className="player-bar-artist">{current.artist}</div> : null}
      </div>
      <span className="player-bar-time">{fmtTime(time)}</span>
      <canvas ref={canvasRef} className="player-bar-wave" />
      <span className="player-bar-time">{fmtTime(dur)}</span>
      <button className="player-bar-close" onClick={stop} aria-label="Close player">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M1 1 L13 13 M13 1 L1 13" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
      </button>
    </div>
  );
}
