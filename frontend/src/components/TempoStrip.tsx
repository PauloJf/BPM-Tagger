import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { BUCKET, fill, type BpmBucket } from "./BpmHistogram";

interface Preset { name: string; bpm: number }

const overlaps = (aLo: number, aHi: number, bLo: number, bHi: number) => aLo < bHi && aHi > bLo;
const fmt = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(1));

/** How a BPM range [lo, hi) relates to a tempo window: 1 = inside it, 2 = only by
 *  half/double time (when that's on), 0 = outside. With no window (`target` not
 *  positive) everything counts as inside. One rule for the library histogram and
 *  every other chart that lights "what fits", so they never disagree. */
export function windowReach(
  lo: number, hi: number,
  w: { target: number; tol: number; cadence: boolean },
): 0 | 1 | 2 {
  if (!(w.target > 0)) return 1;
  const wLo = w.target - w.tol;
  const wHi = w.target + w.tol;
  if (overlaps(lo, hi, wLo, wHi)) return 1;
  if (w.cadence && (overlaps(lo, hi, wLo / 2, wHi / 2) || overlaps(lo, hi, wLo * 2, wHi * 2))) return 2;
  return 0;
}

/** The URL after setting the tempo filter: an empty BPM clears it (and its
 *  tolerance), anything else sets both. Resets paging. Shared by every Library
 *  view that hosts a TempoStrip. */
export function tempoParams(prev: URLSearchParams, next: { bpm: string; tol: string }): URLSearchParams {
  const p = new URLSearchParams(prev);
  const bv = next.bpm.trim();
  if (bv) {
    p.set("bpm", bv);
    p.set("bpm_tol", next.tol.trim() || "5");
  } else {
    p.delete("bpm");
    p.delete("bpm_tol");
  }
  if (p.has("page")) p.set("page", "1");
  return p;
}

/** The Library's tempo filter: the whole library's BPM histogram with the current
 *  window lit, run-preset chips, and the exact BPM / ± inputs.
 *
 *  Everything here is the existing `bpm` / `bpm_tol` / `bpm_cadence` URL filter —
 *  the strip only gives it a face. Clicking a bar centres the window on that
 *  5-BPM bucket; a preset chip centres it on the preset's cadence; Any clears it.
 *  Bars the window reaches only by half/double time (when that's on) are drawn
 *  softer than direct hits, so "why is this 77 BPM song here" is answered by the
 *  chart itself. */
export default function TempoStrip({ bpm, tol, cadence, total, fitLabel, onApply, onCadence }: {
  bpm: string;
  tol: string;
  cadence: boolean;
  /** Tracks matching the whole current view, for the "N tracks" readout. */
  total: number | undefined;
  /** Replaces the "N tracks fit" readout, for views that count something else. */
  fitLabel?: string;
  onApply: (next: { bpm: string; tol: string }) => void;
  onCadence: (on: boolean) => void;
}) {
  // Both queries share a cache entry with another page (Stats, Playlists); the
  // strip just reads the slice it needs. Failure is silent — the inputs still work.
  const distQ = useQuery({
    queryKey: ["stats"],
    queryFn: () => api.get<{ bpm_distribution: BpmBucket[] }>("/api/stats"),
    select: (d) => d.bpm_distribution,
    staleTime: 60_000,
    retry: false,
  });
  const presetsQ = useQuery({
    queryKey: ["run-readiness"],
    queryFn: () => api.get<{ presets: Preset[] }>("/api/run/readiness"),
    select: (d) => d.presets,
    staleTime: 60_000,
    retry: false,
  });

  // Typing is debounced into the URL; clicks apply at once.
  const [bpmText, setBpmText] = useState(bpm);
  const [tolText, setTolText] = useState(tol);
  useEffect(() => setBpmText(bpm), [bpm]);
  useEffect(() => setTolText(tol), [tol]);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  function typed(next: { bpm?: string; tol?: string }) {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => onApply({ bpm: next.bpm ?? bpmText, tol: next.tol ?? tolText }), 300);
  }

  const target = Number(bpm) || 0;
  const t = Number(tol) || 0;
  const active = target > 0;
  const lo = target - t;
  const hi = target + t;

  const reach = (bucketLo: number) => windowReach(bucketLo, bucketLo + BUCKET, { target, tol: t, cadence });

  const bars = fill(distQ.data ?? []);
  const maxCount = bars.length ? Math.max(1, ...bars.map((b) => b.count)) : 1;
  const presets = presetsQ.data ?? [];

  return (
    <section className="tempo-strip" aria-label="Tempo filter">
      <div className="tempo-head">
        <div className="tempo-read">
          <span className="tempo-label">Tempo</span>
          <span className="tempo-range">{active ? `${fmt(lo)}–${fmt(hi)} BPM` : "Any tempo"}</span>
          {active && fitLabel != null && <span className="tempo-fit">{fitLabel}</span>}
          {active && fitLabel == null && total != null && <span className="tempo-fit">{total.toLocaleString()} {total === 1 ? "track" : "tracks"} fit</span>}
        </div>
        <div className="tempo-presets">
          {presets.map((p) => (
            <button
              key={p.bpm}
              type="button"
              className="tempo-chip"
              aria-pressed={target === p.bpm}
              onClick={() => onApply({ bpm: String(p.bpm), tol })}
            >
              {p.name} <span>{p.bpm}</span>
            </button>
          ))}
          <button type="button" className="tempo-chip" aria-pressed={!active} onClick={() => onApply({ bpm: "", tol })}>
            Any
          </button>
        </div>
        <div className="tempo-inputs">
          <input
            type="number" placeholder="BPM" aria-label="Target BPM" min={1} max={300} step={1}
            value={bpmText} autoComplete="off" className="tempo-num"
            onChange={(e) => { setBpmText(e.target.value); typed({ bpm: e.target.value }); }}
          />
          <span className="tempo-pm" aria-hidden="true">±</span>
          <input
            type="number" aria-label="BPM tolerance" min={0} max={50} step={1}
            value={tolText} autoComplete="off" className="tempo-num tempo-num--tol"
            onChange={(e) => { setTolText(e.target.value); typed({ tol: e.target.value }); }}
          />
        </div>
        <label
          className="tempo-cadence"
          data-disabled={!active}
          title="Also match half- and double-time tracks (running cadence)"
        >
          <input type="checkbox" checked={cadence} disabled={!active} onChange={(e) => onCadence(e.target.checked)} />
          half / double time
        </label>
      </div>

      {bars.length > 0 && (
        <div className="tempo-bars" role="group" aria-label="Library BPM distribution" data-active={active}>
          {bars.map((b) => {
            const r = reach(b.bpm);
            return (
              <button
                key={b.bpm}
                type="button"
                className="tempo-bar"
                data-reach={r}
                aria-label={`${b.bpm} to ${b.bpm + BUCKET} BPM, ${b.count} ${b.count === 1 ? "track" : "tracks"}`}
                title={`${b.bpm}–${b.bpm + BUCKET} BPM · ${b.count}`}
                style={{ height: `${Math.max(6, Math.round((b.count / maxCount) * 100))}%` }}
                onClick={() => onApply({ bpm: String(Math.round(b.bpm + BUCKET / 2)), tol })}
              />
            );
          })}
        </div>
      )}
      {bars.length > 0 && (
        <div className="tempo-axis" aria-hidden="true">
          <span>{bars[0].bpm}</span>
          <span>{bars[bars.length - 1].bpm + BUCKET}</span>
        </div>
      )}
    </section>
  );
}
