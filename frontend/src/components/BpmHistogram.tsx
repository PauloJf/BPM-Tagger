/** The BPM histogram, shared by the Stats page (library-wide) and the playlist
 *  detail page's stats strip (that playlist's matched tracks).
 *
 *  One component so the two never drift: same 5-BPM buckets from the backend,
 *  same bar colouring (peak bucket accented, the 100–160 running band tinted,
 *  everything else soft), same optional median rule. `mini` only shrinks it —
 *  the marks are identical, so a playlist's shape can be read against the
 *  library's without re-learning the chart.
 *
 *  The axis is the DATA's range, not a fixed one: a playlist of 150–170 BPM
 *  tracks is drawn over 150–170, with the empty buckets inside it filled so a
 *  bar's position still means a BPM. */

/** One 5-BPM bucket: `bpm` is the bucket's lower bound. */
export interface BpmBucket {
  bpm: number;
  count: number;
}

const BUCKET = 5;

/** The buckets to draw: the backend only returns the ones that hold tracks, so
 *  the gaps between them are filled in here before anything is laid out.
 *
 *  The bars are equal-width flex children, so a bar's position only means a BPM
 *  if every step between the first bucket and the last is present — otherwise a
 *  playlist of four tight clusters would stretch across the whole axis and put
 *  its peak over the wrong label, and a bimodal library would draw its two
 *  clumps touching. Filling makes position linear in BPM, which is what lets the
 *  axis labels below be read off the range. */
function fill(dist: BpmBucket[]): BpmBucket[] {
  if (!dist.length) return [];
  const lo = dist[0].bpm;
  const span = Math.max(0, dist[dist.length - 1].bpm - lo);
  // The cap is pure defence against a nonsense row; detected BPM is normalized
  // into [BPM_MIN, BPM_MAX] server-side, so a real library is far inside it.
  const n = Math.min(200, Number.isFinite(span) ? Math.round(span / BUCKET) + 1 : 1);
  const out = Array.from({ length: n }, (_, i) => ({ bpm: lo + i * BUCKET, count: 0 }));
  for (const d of dist) {
    const i = Math.min(n - 1, Math.max(0, Math.round((d.bpm - lo) / BUCKET)));
    out[i].count += d.count;
  }
  return out;
}

export default function BpmHistogram({ dist, median, mini, emptyText = "No data yet." }: {
  dist: BpmBucket[];
  /** Draws the median rule, positioned across the bucket range. Omit to hide it. */
  median?: number | null;
  /** Compact variant for the playlist strip: shorter body, smaller axis labels. */
  mini?: boolean;
  emptyText?: string;
}) {
  const bars = fill(dist);
  const maxCount = bars.length ? Math.max(1, ...bars.map((x) => x.count)) : 1;
  const peakBpm = bars.length ? bars.reduce((a, x) => (x.count > a.count ? x : a), bars[0]).bpm : null;
  const bpmMin = bars.length ? bars[0].bpm : 0;
  const bpmMax = bars.length ? bars[bars.length - 1].bpm + BUCKET : 1;
  const medianPct = median != null && bars.length
    ? Math.max(0, Math.min(100, ((median - bpmMin) / (bpmMax - bpmMin)) * 100))
    : null;
  // Ticks off the drawn range rather than a fixed 60–200 scale: the axis is
  // whatever this data spans, so a narrow playlist gets its own labels instead
  // of borrowing the library's.
  const tickCount = mini ? 3 : 5;
  const ticks = bars.length
    ? Array.from({ length: tickCount }, (_, i) =>
        Math.round(bpmMin + ((bpmMax - bpmMin) * i) / (tickCount - 1)))
    : [];

  return (
    <>
      <div className="histogram-wrap" style={mini ? { height: 84 } : undefined}>
        <div className="hist-gridline" style={{ bottom: "75%" }} />
        <div className="hist-gridline" style={{ bottom: "50%" }} />
        <div className="hist-gridline" style={{ bottom: "25%" }} />
        {bars.length ? (
          <>
            {bars.map((dd, i) => (
              <div
                key={i}
                className="hist-bar"
                title={`${dd.bpm}–${dd.bpm + BUCKET} BPM · ${dd.count}${dd.bpm === peakBpm ? " (peak)" : ""}`}
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
      {ticks.length > 0 && (
        <div className="hist-x-labels" style={mini ? { fontSize: 9, marginTop: 4 } : undefined}>
          {ticks.map((t, i) => <span key={i}>{t}</span>)}
        </div>
      )}
    </>
  );
}
