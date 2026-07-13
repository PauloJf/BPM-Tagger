// Mono BPM number at sizePx. When pulsing and bpm is set, shows a two-layer
// pulsing accent dot whose animation-duration is beatMs. Ported from _macros.
export function BpmDisplay({
  bpm,
  sizePx = 84,
  pulsing = false,
  beatMs = 500,
  dotPx,
}: {
  bpm: number | null;
  sizePx?: number;
  pulsing?: boolean;
  beatMs?: number;
  /** Override the pulsing-dot diameter (defaults to 14% of sizePx — too small
   *  for compact placements like the player bar). */
  dotPx?: number;
}) {
  const dotSize = dotPx ?? Number((sizePx * 0.14).toFixed(1));
  const gap = Number((sizePx * 0.16).toFixed(1));
  const unitPx = Math.round(sizePx * 0.22);
  const showDot = pulsing && !!bpm;
  return (
    <span
      className="bpm-display-comp"
      style={{
        fontFamily: "var(--mono)",
        fontWeight: 600,
        fontSize: sizePx,
        lineHeight: 0.95,
        letterSpacing: "-0.04em",
        color: "var(--text)",
        fontVariantNumeric: "tabular-nums",
        display: "inline-flex",
        alignItems: "center",
        gap,
      }}
    >
      {showDot && (
        <span style={{ display: "inline-block", position: "relative", width: dotSize, height: dotSize, flexShrink: 0 }}>
          <span style={{ position: "absolute", inset: 0, borderRadius: 999, background: "var(--accent)", opacity: 0.55 }} />
          <span style={{ position: "absolute", inset: 0, borderRadius: 999, background: "var(--accent)", animation: `pulse-beat ${beatMs}ms ease-out infinite` }} />
        </span>
      )}
      <span style={{ display: "inline-flex", alignItems: "baseline", gap: 8 }}>
        {bpm ? bpm.toFixed(1) : "—"}
        <span style={{ fontSize: unitPx, color: "var(--muted)", fontWeight: 500, letterSpacing: "0.06em" }}>BPM</span>
      </span>
    </span>
  );
}
