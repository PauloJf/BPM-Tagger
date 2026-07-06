import { usePlayer } from "../lib/player";
import { fmtTime, useAudioTime } from "../hooks/useAudioTime";

export default function PlayerBar() {
  const { current, playing, audioRef, toggle, stop } = usePlayer();
  const { time, dur } = useAudioTime(audioRef);
  if (!current) return null;

  const pct = dur > 0 ? (time / dur) * 100 : 0;
  const seek = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    if (audioRef.current && dur) audioRef.current.currentTime = ratio * dur;
  };

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
      <div className="player-bar-track" onClick={seek}>
        <div className="player-bar-fill" style={{ width: `${pct}%` }} />
      </div>
      <span className="player-bar-time">{fmtTime(dur)}</span>
      <button className="player-bar-close" onClick={stop} aria-label="Close player">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M1 1 L13 13 M13 1 L1 13" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
      </button>
    </div>
  );
}
