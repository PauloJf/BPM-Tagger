/** The classic "now playing" equalizer bars, but beat-paced: each bar bounces
 *  with a period of one or two beats of what's audible (the locked cadence in
 *  Run mode, the track's native BPM otherwise), so the ensemble moves with the
 *  music rather than at an arbitrary CSS tempo. Very fast cadences are folded
 *  down an octave (≥300 ms per bounce) so the bars never flicker. Pure CSS
 *  (`eq-bounce` keyframes in design-system.css) — free, and it survives being
 *  portaled into the PiP mini-player window, whose stylesheets are cloned.
 *  Not playing → the bars freeze into a dimmed static staircase, keeping the
 *  footprint identical so pausing never shifts layout. Decorative only. */
export function EqBars({
  playing,
  beatMs,
  size = 14,
  color = "var(--accent)",
  style,
}: {
  playing: boolean;
  /** Milliseconds per audible beat; omit when BPM is unknown (falls back to a
   *  relaxed non-beat tempo). */
  beatMs?: number;
  /** Bar height in px; width scales from it. */
  size?: number;
  color?: string;
  style?: React.CSSProperties;
}) {
  let beat = beatMs && beatMs > 0 ? beatMs : 600;
  while (beat < 300) beat *= 2;
  const barW = Math.max(2, Math.round(size / 6));
  // h: static (paused) pose · beats: bounce period · phase: offset into it.
  // Mixed periods keep the motion organic; the pattern repeats every 2 beats.
  const bars = [
    { h: 0.55, beats: 2, phase: 0.0 },
    { h: 0.95, beats: 1, phase: 0.4 },
    { h: 0.7, beats: 2, phase: 0.65 },
    { h: 0.85, beats: 1, phase: 0.15 },
  ];
  return (
    <span
      aria-hidden
      style={{ display: "inline-flex", alignItems: "flex-end", gap: barW, height: size, opacity: playing ? 1 : 0.45, flexShrink: 0, ...style }}
    >
      {bars.map((b, i) => {
        const dur = Math.round(beat * b.beats);
        return (
          <span
            key={i}
            style={{
              width: barW,
              height: size,
              borderRadius: barW,
              background: color,
              transformOrigin: "50% 100%",
              transform: playing ? undefined : `scaleY(${b.h})`,
              animation: playing ? `eq-bounce ${dur}ms ease-in-out ${-Math.round(dur * b.phase)}ms infinite` : "none",
            }}
          />
        );
      })}
    </span>
  );
}
