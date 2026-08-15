/** The BPM histogram, shared by the Stats page (library-wide) and the playlist
 *  detail page's stats strip (that playlist's matched tracks).
 *
 *  One component so the two never drift: same 5-BPM buckets from the backend,
 *  same bar colouring (peak bucket accented, the 100–160 running band tinted,
 *  everything else soft), same optional median rule. `mini` only shrinks it —
 *  the marks and the scale are identical, so a playlist's shape can be read
 *  against the library's without re-learning the chart. */

/** One 5-BPM bucket: `bpm` is the bucket's lower bound. */
export interface BpmBucket {
  bpm: number;
  count: number;
}

export default function BpmHistogram({ dist, median, mini, emptyText = "No data yet." }: {
  dist: BpmBucket[];
  /** Draws the median rule, positioned across the bucket range. Omit to hide it. */
  median?: number | null;
  /** Compact variant for the playlist strip: shorter body, smaller axis labels. */
  mini?: boolean;
  emptyText?: string;
}) {
  const maxCount = dist.length ? Math.max(1, ...dist.map((x) => x.count)) : 1;
  const peakBpm = dist.length ? dist.reduce((a, x) => (x.count > a.count ? x : a), dist[0]).bpm : null;
  const bpmMin = dist.length ? dist[0].bpm : 0;
  const bpmMax = dist.length ? dist[dist.length - 1].bpm + 5 : 1;
  const medianPct = median != null && dist.length
    ? Math.max(0, Math.min(100, ((median - bpmMin) / (bpmMax - bpmMin)) * 100))
    : null;

  return (
    <>
      <div className="histogram-wrap" style={mini ? { height: 84 } : undefined}>
        <div className="hist-gridline" style={{ bottom: "75%" }} />
        <div className="hist-gridline" style={{ bottom: "50%" }} />
        <div className="hist-gridline" style={{ bottom: "25%" }} />
        {dist.length ? (
          <>
            {dist.map((dd, i) => (
              <div
                key={i}
                className="hist-bar"
                title={`${dd.bpm}–${dd.bpm + 5} BPM · ${dd.count}${dd.bpm === peakBpm ? " (peak)" : ""}`}
                style={{
                  height: `${(dd.count / maxCount) * 100}%`,
                  background: dd.bpm === peakBpm ? "var(--accent-2)" : dd.bpm >= 100 && dd.bpm <= 160 ? "var(--accent)" : "var(--accent-soft-strong)",
                }}
              />
            ))}
            {medianPct != null && (
              <div title={`Median: ${median!.toFixed(1)} BPM`} style={{ position: "absolute", top: 0, bottom: 0, width: 2, background: "var(--warn-fg)", opacity: 0.8, left: `${medianPct}%`, pointerEvents: "none", borderRadius: 1 }} />
            )}
          </>
        ) : (
          <p style={{ color: "var(--muted)", fontSize: 13, alignSelf: "center", padding: 20 }}>{emptyText}</p>
        )}
      </div>
      <div className="hist-x-labels" style={mini ? { fontSize: 9, marginTop: 4 } : undefined}>
        <span>60</span>
        <span>90</span>
        <span>120</span>
        <span>150</span>
        <span>180</span>
        <span>200</span>
      </div>
    </>
  );
}
