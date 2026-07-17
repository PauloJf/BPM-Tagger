import { useRef } from "react";
import { usePlayer, type TempoLock } from "../lib/player";
import { BpmDisplay } from "./BpmDisplay";
import { Cover } from "./Artwork";
import { fmtTime, useAudioTime } from "../hooks/useAudioTime";

// Kept in sync with Run.tsx — the target the tempo lock lands on, saved there.
const TARGET_KEY = "bpm.run.target";

/** Rebuild a tempo lock when re-enabling it from the mini player with no prior
 *  lock to restore: target from the Run page's saved value, sensible defaults
 *  for the rest (the Run page corrects octave/stretch on its next visit). */
function lockFromStorage(): TempoLock {
  const saved = Number(localStorage.getItem(TARGET_KEY));
  const target = saved >= 30 && saved <= 300 ? saved : 155;
  return { target, octave: true, stretchLimitPct: 15 };
}

const ctlBtn: React.CSSProperties = {
  width: 38, height: 38, borderRadius: 999, display: "inline-flex",
  alignItems: "center", justifyContent: "center", background: "var(--surface)",
  border: "1px solid var(--border)", color: "var(--text)", cursor: "pointer",
  flexShrink: 0,
};

/** Compact now-playing card rendered inside a Document Picture-in-Picture
 *  window (see lib/miniPlayer). Pure UI — playback stays in the main tab's
 *  <audio>; this just reads and drives player state: transport, a seekable
 *  progress bar, volume, and (for Run mode) the merged BPM+lock pill that shows
 *  the locked target cadence with a pulsing beat dot and toggles the tempo lock,
 *  falling back to the track's native BPM when nothing is locked. */
export default function MiniPlayer() {
  const { current, playing, toggle, next, prev, hasQueue, tempoLock, setTempoLock,
          audioRef, volume, setVolume } = usePlayer();
  const { time, dur } = useAudioTime(audioRef);
  // Remember the last active lock so releasing then re-arming restores its exact
  // target/octave/stretch instead of the storage-derived defaults.
  const lastLock = useRef<TempoLock | null>(tempoLock);
  if (tempoLock) lastLock.current = tempoLock;

  if (!current) {
    return (
      <div style={{ display: "grid", placeItems: "center", height: "100vh", color: "var(--muted)", fontSize: 13, fontFamily: "var(--mono)" }}>
        Nothing playing
      </div>
    );
  }

  const locked = !!tempoLock;
  // Locked → the cadence your feet follow (the target); otherwise the track's
  // native BPM. Null when unknown (e.g. a preview clip) → the pill is hidden.
  const cadence = locked ? tempoLock!.target : current.bpm ?? null;
  const pct = dur > 0 ? (time / dur) * 100 : 0;

  function toggleLock() {
    if (tempoLock) setTempoLock(null);
    else setTempoLock(lastLock.current ?? lockFromStorage());
  }

  function seek(e: React.MouseEvent<HTMLDivElement>) {
    const a = audioRef.current;
    if (!a || !a.duration) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    a.currentTime = ratio * a.duration;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", padding: 12, gap: 9, boxSizing: "border-box" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flex: 1, minHeight: 0 }}>
        {!current.ephemeral && (
          <Cover
            path={current.path}
            size={56}
            style={{ borderRadius: 10, flexShrink: 0, objectFit: "cover", boxShadow: "0 6px 18px -8px rgba(0,0,0,0.6)" }}
          />
        )}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 14, fontWeight: 600, letterSpacing: "-0.01em", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {current.title}
          </div>
          {current.artist && (
            <div style={{ fontSize: 12, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", marginTop: 2 }}>
              {current.artist}
            </div>
          )}
        </div>
        {cadence != null && (
          <button
            onClick={toggleLock}
            aria-pressed={locked}
            title={locked
              ? `Tempo locked to ${tempoLock!.target} BPM — click to release`
              : "Tempo unlocked — click to lock onto your run cadence"}
            style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "6px 11px 6px 9px", borderRadius: 999, cursor: "pointer", flexShrink: 0,
              background: locked ? "var(--accent-soft)" : "var(--surface)",
              border: `1px solid ${locked ? "var(--accent-border)" : "var(--border)"}`,
              color: locked ? "var(--accent-2)" : "var(--muted)",
            }}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
              {locked
                ? <><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></>
                : <><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 7.5-1.9" /></>}
            </svg>
            <BpmDisplay bpm={cadence} sizePx={16} dotPx={8} pulsing={playing} beatMs={Math.round(60000 / cadence)} />
          </button>
        )}
      </div>

      {/* Seekable progress + elapsed / remaining. */}
      <div style={{ flexShrink: 0 }}>
        <div
          onClick={seek}
          role="progressbar"
          aria-label="Seek"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(pct)}
          style={{ height: 6, borderRadius: 999, background: "var(--border)", cursor: dur > 0 ? "pointer" : "default", overflow: "hidden" }}
        >
          <div style={{ width: `${pct}%`, height: "100%", background: "var(--accent)", borderRadius: 999 }} />
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)", marginTop: 3 }}>
          <span>{fmtTime(time)}</span>
          <span>-{fmtTime(Math.max(0, dur - time))}</span>
        </div>
      </div>

      <div style={{ display: "flex", justifyContent: "center", alignItems: "center", gap: 16, flexShrink: 0, position: "relative" }}>
        {hasQueue && (
          <button style={ctlBtn} onClick={prev} aria-label="Previous" title="Previous">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M6 5v14h2V5H6zm3 7l11 7V5l-11 7z" /></svg>
          </button>
        )}
        <button
          style={{ ...ctlBtn, width: 48, height: 48, background: "var(--accent)", border: "none", color: "white", boxShadow: "0 10px 30px -10px var(--accent)" }}
          onClick={toggle}
          aria-label={playing ? "Pause" : "Play"}
        >
          {playing ? (
            <svg width="20" height="20" viewBox="0 0 24 24" fill="white"><rect x="6" y="4" width="4" height="16" rx="1" /><rect x="14" y="4" width="4" height="16" rx="1" /></svg>
          ) : (
            <svg width="20" height="20" viewBox="0 0 24 24" fill="white" style={{ marginLeft: 2 }}><polygon points="6,4 20,12 6,20" /></svg>
          )}
        </button>
        {hasQueue && (
          <button style={ctlBtn} onClick={next} aria-label="Next" title="Next">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M16 5v14h2V5h-2zM4 19l11-7L4 5v14z" /></svg>
          </button>
        )}
        {/* Volume pinned right, matching the player bar's control. */}
        <span
          title={`Volume ${Math.round(volume * 100)}%`}
          style={{ position: "absolute", right: 0, display: "inline-flex", alignItems: "center", gap: 5 }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" style={{ color: "var(--muted)", flexShrink: 0 }}>
            <path d="M3 9v6h4l5 5V4L7 9H3z" />{volume > 0.05 && <path d="M16 8a5 5 0 0 1 0 8" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />}
          </svg>
          <input
            type="range" min={0} max={1} step={0.01} value={volume}
            onChange={(e) => setVolume(+e.target.value)}
            aria-label="Volume"
            style={{ width: 62 }}
          />
        </span>
      </div>
    </div>
  );
}
