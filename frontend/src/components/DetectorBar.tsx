import type { Track } from "../lib/types";

// SVG axis 60–200 BPM. Ticks at 60/90/120/150/180.
// DR=accent  ES=accent-2  LB=info-fg. Final BPM = glowing vertical bar.
// Ported verbatim from the _macros.html detector_bar macro.
const R = 140.0;
const x = (bpm: number) => Number((((bpm - 60) / R) * 300).toFixed(2));

function Marker({ bpm, color, label }: { bpm: number; color: string; label: string }) {
  const cx = x(bpm);
  return (
    <>
      <circle cx={cx} cy="22" r="5" fill="var(--bg)" stroke={color} strokeWidth="2" />
      <text x={cx} y="13" textAnchor="middle" fontFamily="var(--mono)" fontSize="8" fontWeight="600" fill={color} letterSpacing="0.04em">
        {label}
      </text>
    </>
  );
}

export function DetectorBar({ track }: { track: Track }) {
  return (
    <div className="detector-bar-wrap">
      <svg viewBox="0 0 300 48" width="100%" height="48" style={{ overflow: "visible", display: "block" }}>
        <line x1="0" y1="32" x2="300" y2="32" stroke="var(--border)" strokeWidth="1.5" />
        {[60, 90, 120, 150, 180].map((tick) => {
          const tx = x(tick);
          return (
            <g key={tick}>
              <line x1={tx} y1="29" x2={tx} y2="37" stroke="var(--border)" strokeWidth="1" />
              <text x={tx} y="47" textAnchor="middle" fontFamily="var(--mono)" fontSize="8" fill="var(--muted)">
                {tick}
              </text>
            </g>
          );
        })}
        {track.bpm ? (
          <rect x={x(track.bpm) - 1} y="16" width="2.5" height="24" rx="1.25" fill="var(--accent)" filter="drop-shadow(0 0 4px var(--accent-glow))" />
        ) : null}
        {track.bpm_dr ? <Marker bpm={track.bpm_dr} color="var(--accent)" label="DR" /> : null}
        {track.bpm_es ? <Marker bpm={track.bpm_es} color="var(--accent-2)" label="ES" /> : null}
        {track.bpm_lb ? <Marker bpm={track.bpm_lb} color="var(--info-fg)" label="LB" /> : null}
      </svg>
    </div>
  );
}
