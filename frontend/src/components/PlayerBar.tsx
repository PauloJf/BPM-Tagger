import { useRef } from "react";
import { usePlayer } from "../lib/player";
import { fmtTime, useAudioTime } from "../hooks/useAudioTime";
import { useWaveform } from "../hooks/useWaveform";

export default function PlayerBar() {
  const { current, playing, error, audioRef, toggle, stop,
          queue, queueIndex, hasQueue, shuffle, repeat, previewing, next, prev, toggleShuffle, cycleRepeat } = usePlayer();
  const { time, dur } = useAudioTime(audioRef);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  // Hooks must run unconditionally; disabled until a track is loaded.
  useWaveform(canvasRef, audioRef, current?.path || "", !!current, !!current);

  if (!current) return null;

  const repeatTitle = repeat === "off" ? "Repeat: off" : repeat === "all" ? "Repeat: all" : "Repeat: one";

  return (
    <div className="player-bar">
      {hasQueue && (
        <button className="player-bar-ctl" onClick={prev} aria-label="Previous" title="Previous">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M6 5v14h2V5H6zm3 7l11 7V5l-11 7z" /></svg>
        </button>
      )}
      <button className="player-bar-play" onClick={toggle} aria-label={playing ? "Pause" : "Play"}>
        {playing ? (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="white"><rect x="6" y="4" width="4" height="16" rx="1" /><rect x="14" y="4" width="4" height="16" rx="1" /></svg>
        ) : (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="white"><polygon points="6,4 20,12 6,20" /></svg>
        )}
      </button>
      {hasQueue && (
        <button className="player-bar-ctl" onClick={next} aria-label="Next" title="Next">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M16 5v14h2V5h-2zM4 19l11-7L4 5v14z" /></svg>
        </button>
      )}
      <div className="player-bar-meta">
        <div className="player-bar-title">{current.title}</div>
        {error ? (
          <div className="player-bar-artist" style={{ color: "var(--err-fg)" }}>{error}</div>
        ) : previewing ? (
          <div className="player-bar-artist" style={{ color: "var(--accent-2)" }}>Preview · returns to queue</div>
        ) : current.artist ? (
          <div className="player-bar-artist">{current.artist}</div>
        ) : hasQueue ? (
          <div className="player-bar-artist">{queueIndex + 1} / {queue.length}</div>
        ) : null}
      </div>
      <span className="player-bar-time">{fmtTime(time)}</span>
      <canvas ref={canvasRef} className="player-bar-wave" />
      <span className="player-bar-time">{fmtTime(dur)}</span>
      <button
        className={"player-bar-ctl player-bar-ctl--optional" + (shuffle ? " active" : "")}
        onClick={toggleShuffle}
        aria-label="Shuffle"
        aria-pressed={shuffle}
        title={shuffle ? "Shuffle: on" : "Shuffle: off"}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M16 3h5v5M4 20L21 3M21 16v5h-5M15 15l6 6M4 4l5 5" />
        </svg>
      </button>
      <button
        className={"player-bar-ctl player-bar-ctl--optional" + (repeat !== "off" ? " active" : "")}
        onClick={cycleRepeat}
        aria-label={repeatTitle}
        title={repeatTitle}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M17 1l4 4-4 4" /><path d="M3 11V9a4 4 0 0 1 4-4h14" /><path d="M7 23l-4-4 4-4" /><path d="M21 13v2a4 4 0 0 1-4 4H3" />
        </svg>
        {repeat === "one" && <span className="player-bar-ctl-badge">1</span>}
      </button>
      <button className="player-bar-close" onClick={stop} aria-label="Close player">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M1 1 L13 13 M13 1 L1 13" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
      </button>
    </div>
  );
}
