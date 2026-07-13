import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { usePlayer } from "../lib/player";
import { fmtTime, useAudioTime } from "../hooks/useAudioTime";
import { useWaveform } from "../hooks/useWaveform";
import { BpmDisplay } from "./BpmDisplay";
import { LyricsPanel } from "./LyricsPanel";

export default function PlayerBar() {
  const { current, playing, error, audioRef, toggle, stop,
          orderedQueue, orderPos, hasQueue, shuffle, repeat, previewing, volume, setVolume,
          next, prev, jumpTo, removeAt, moveAt, toggleShuffle, cycleRepeat, tempoLock } = usePlayer();
  const { time, dur } = useAudioTime(audioRef);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [queueOpen, setQueueOpen] = useState(false);
  const [lyricsOpen, setLyricsOpen] = useState(false);
  // Hooks must run unconditionally; disabled until a track is loaded.
  useWaveform(canvasRef, audioRef, current?.path || "", !!current, !!current);
  // Current track's BPM for the pulsing indicator. Own cache key — TrackDetail's
  // ["track", path] entry carries review-queue navigation this fetch would clobber.
  const bpmQ = useQuery({
    queryKey: ["track-bpm", current?.path || ""],
    queryFn: () => api.get<{ track: { bpm: number | null } }>(
      `/api/track?path=${encodeURIComponent(current!.path)}`),
    enabled: !!current,
    staleTime: 60_000,
  });
  const bpm = bpmQ.data?.track?.bpm ?? null;

  if (!current) return null;

  const repeatTitle = repeat === "off" ? "Repeat: off" : repeat === "all" ? "Repeat: all" : "Repeat: one";

  return (
    <div className="player-bar">
      {lyricsOpen && (
        <LyricsPanel path={current.path} audioRef={audioRef} onClose={() => setLyricsOpen(false)} />
      )}
      {queueOpen && hasQueue && (
        <div className="player-queue">
          <div className="player-queue-head">
            <span>Queue · {orderedQueue.length}</span>
            <span style={{ display: "flex", gap: 4 }}>
              <button className="btn btn-bare btn-sm" onClick={() => { stop(); setQueueOpen(false); }} title="Clear the queue">Clear</button>
              <button className="btn btn-bare btn-sm" onClick={() => setQueueOpen(false)} aria-label="Close queue">✕</button>
            </span>
          </div>
          <div className="player-queue-list">
            {orderedQueue.map((t, i) => (
              <div key={`${t.path}-${i}`} className={"player-queue-row" + (i === orderPos ? " current" : "")}>
                <button className="player-queue-title" title={t.title} onClick={() => jumpTo(i)}>
                  {i === orderPos && <span style={{ color: "var(--accent-2)", marginRight: 6 }}>▶</span>}
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.title}</span>
                  {t.artist && <span style={{ color: "var(--muted)" }}> · {t.artist}</span>}
                </button>
                <div className="player-queue-actions">
                  <button className="btn btn-bare btn-sm" disabled={i === 0} onClick={() => moveAt(i, -1)} aria-label="Move up" title="Move up">↑</button>
                  <button className="btn btn-bare btn-sm" disabled={i === orderedQueue.length - 1} onClick={() => moveAt(i, 1)} aria-label="Move down" title="Move down">↓</button>
                  <button className="btn btn-bare btn-sm" onClick={() => removeAt(i)} aria-label="Remove" title="Remove from queue">✕</button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
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
        <Link
          to={`/track?path=${encodeURIComponent(current.path)}`}
          className="player-bar-title"
          title={`Open ${current.title}`}
        >
          {current.title}
        </Link>
        {error ? (
          <div className="player-bar-artist" style={{ color: "var(--err-fg)" }}>{error}</div>
        ) : previewing ? (
          <div className="player-bar-artist" style={{ color: "var(--accent-2)" }}>Preview · returns to queue</div>
        ) : current.artist ? (
          <Link to={`/artist?name=${encodeURIComponent(current.artist)}`} className="player-bar-artist" style={{ color: "inherit", textDecoration: "none" }} title={`View ${current.artist}`}>
            {current.artist}
          </Link>
        ) : null}
      </div>
      {tempoLock ? (
        // Run mode: the readout shows the locked target cadence, not the
        // track's native BPM — that's what your feet follow.
        <Link
          to="/run"
          className="player-bar-bpm"
          style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 5 }}
          title={`Tempo locked to ${tempoLock.target} BPM — open Run mode`}
        >
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="var(--accent-2)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
            <rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" />
          </svg>
          <BpmDisplay bpm={tempoLock.target} sizePx={17} dotPx={9} pulsing={playing} beatMs={Math.round(60000 / tempoLock.target)} />
        </Link>
      ) : bpm != null && (
        <span className="player-bar-bpm" title={`${bpm.toFixed(1)} BPM — the dot pulses on the beat`}>
          <BpmDisplay bpm={bpm} sizePx={17} dotPx={9} pulsing={playing} beatMs={Math.round(60000 / bpm)} />
        </span>
      )}
      <button
        className={"player-bar-ctl" + (lyricsOpen ? " active" : "")}
        onClick={() => { setLyricsOpen((o) => !o); setQueueOpen(false); }}
        title="Lyrics"
        aria-label="Show lyrics"
        aria-expanded={lyricsOpen}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
          <path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v3" />
        </svg>
      </button>
      {hasQueue && (
        <button
          className={"player-bar-ctl player-bar-ctl--optional" + (queueOpen ? " active" : "")}
          onClick={() => { setQueueOpen((o) => !o); setLyricsOpen(false); }}
          title="Queue"
          aria-label="Show queue"
          aria-expanded={queueOpen}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M4 6h11M4 12h11M4 18h7M17 14v6M17 20l3-2" />
          </svg>
          <span style={{ fontFamily: "var(--mono)", fontSize: 10, marginLeft: 2 }}>{orderPos + 1}/{orderedQueue.length}</span>
        </button>
      )}
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
      <span className="player-bar-volume player-bar-ctl--optional" title={`Volume ${Math.round(volume * 100)}%`}>
        <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" style={{ color: "var(--muted)", flexShrink: 0 }}>
          <path d="M3 9v6h4l5 5V4L7 9H3z" />{volume > 0.05 && <path d="M16 8a5 5 0 0 1 0 8" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />}
        </svg>
        <input type="range" min={0} max={1} step={0.01} value={volume}
               onChange={(e) => setVolume(+e.target.value)} aria-label="Volume" />
      </span>
      <button className="player-bar-close" onClick={stop} aria-label="Close player">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M1 1 L13 13 M13 1 L1 13" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
      </button>
    </div>
  );
}
