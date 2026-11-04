import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";
import type { PickPreviewResponse, PickWeights } from "../../lib/types";
import { cacheStats, clearOffline, offlineSupported, reconcileIndex } from "../../lib/offline";
import { Toggle } from "../Toggle";
import { SaveButton, fieldLabel, fmtBytes, readers, useSaveSection, useSettings, type Saved } from "./shared";

/** Settings → Playback & Run tab: rating-weighted picking, playback, Run mode. */

const RATING_DEFAULTS = { useRatings: true, weights: [0.1, 0.5, 1, 1, 3, 6] as PickWeights, newFactor: 1 };
const PRESET_DEFAULTS = [{ name: "Warmup", bpm: 120 }, { name: "Easy", bpm: 155 },
                         { name: "Steady", bpm: 165 }, { name: "Tempo", bpm: 175 }];

interface LoudnessFillStatus {
  running: boolean; total: number; done: number;
  measured: number; tagged: number; failed: number; remaining: number;
}

export function RatingsCard() {
  const { cfg } = useSettings();
  const saveSection = useSaveSection();
  // Weights are in the API's fixed order: 1,2,3,unrated,4,5.
  const [ratings, setRatingsCfg] = useState(RATING_DEFAULTS);
  const [ratingsSaved, setRatingsSaved] = useState<Saved>("");
  const [ratingsPreview, setRatingsPreview] = useState<PickPreviewResponse | null>(null);
  useEffect(() => {
    if (!cfg) return;
    const { n, b } = readers(cfg);
    setRatingsCfg({
      useRatings: b("pick_use_ratings", true),
      weights: (Array.isArray(cfg.pick_weights) && cfg.pick_weights.length === 6
        ? (cfg.pick_weights as number[]).map(Number)
        : RATING_DEFAULTS.weights) as PickWeights,
      newFactor: n("pick_new_factor", 1),
    });
  }, [cfg]);

  // Live expected-share preview: debounced so dragging a weight slider doesn't
  // spray requests. Always reads the UNSAVED values in `ratings`.
  const previewTimer = useRef<number | null>(null);
  useEffect(() => {
    if (previewTimer.current) window.clearTimeout(previewTimer.current);
    previewTimer.current = window.setTimeout(() => {
      const sp = new URLSearchParams();
      sp.set("use_ratings", ratings.useRatings ? "1" : "0");
      sp.set("weights", ratings.weights.join(","));
      sp.set("new_factor", String(ratings.newFactor));
      api.get<PickPreviewResponse>(`/api/settings/pick-preview?${sp.toString()}`)
        .then(setRatingsPreview)
        .catch(() => {});
    }, 350);
    return () => { if (previewTimer.current) window.clearTimeout(previewTimer.current); };
  }, [ratings]);

  return (
    <>
      {/* Ratings & picking */}
      <div id="sec-ratings" className="settings-card card">
        <div className="settings-card-header">
          <h2>Ratings &amp; picking</h2>
          <p>
            How 1-5 star ratings weight the odds a track gets picked by Run, Listen shuffle/radio and
            similar. Ratings are per account; these weights are global. "New" means unrated and never
            played by that account. Dislikes are always excluded, and the Run stretch limit is still a
            hard rule — this only weights what's already eligible.
          </p>
        </div>
        <form onSubmit={(e) => {
          e.preventDefault();
          saveSection("/api/settings/ratings", {
            pick_use_ratings: ratings.useRatings,
            pick_weights: ratings.weights,
            pick_new_factor: ratings.newFactor,
          }, setRatingsSaved);
        }}>
          <div className="field-row">
            {fieldLabel("Use ratings", "Off = every non-disliked track weighs the same (a uniform draw) — the new-songs weight below is ignored too.")}
            <Toggle on={ratings.useRatings} onChange={(v) => setRatingsCfg({ ...ratings, useRatings: v })} label="Use ratings" />
          </div>
          <div className="field-row">
            {fieldLabel("Weights", "How much more (or less) likely a level is to be picked than an unrated track (1×). 0 means that level is picked only once nothing else is left.")}
            <div style={{ display: "flex", flexWrap: "wrap", gap: 14 }}>
              {(["1★", "2★", "3★", "Unrated", "4★", "5★"] as const).map((lbl, i) => (
                <div key={lbl} style={{ display: "flex", flexDirection: "column", gap: 4, width: 96 }}>
                  <span style={{ fontSize: 11, color: "var(--muted)", textAlign: "center" }}>{lbl}</span>
                  <input
                    type="number" min={0} max={100} step={0.1}
                    value={ratings.weights[i]}
                    aria-label={`${lbl} weight`}
                    disabled={!ratings.useRatings}
                    onChange={(e) => {
                      const v = Math.max(0, Math.min(100, Number(e.target.value) || 0));
                      const next = [...ratings.weights] as PickWeights;
                      next[i] = v;
                      setRatingsCfg({ ...ratings, weights: next });
                    }}
                    style={{ width: "100%", fontFamily: "var(--mono)", textAlign: "center" }}
                  />
                  <div className="slider-wrap">
                    <input
                      type="range" min={0} max={20} step={0.1}
                      value={Math.min(20, ratings.weights[i])}
                      aria-label={`${lbl} weight slider`}
                      disabled={!ratings.useRatings}
                      onChange={(e) => {
                        const next = [...ratings.weights] as PickWeights;
                        next[i] = Number(e.target.value);
                        setRatingsCfg({ ...ratings, weights: next });
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div className="field-row">
            {fieldLabel("New songs", "Extra weight (multiplied onto the unrated weight) for tracks that account has never played and never rated.")}
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <div className="segmented">
                {[["Never", 0], ["Less", 0.5], ["Neutral", 1], ["More", 2], ["Much more", 4]].map(([lbl, v]) => (
                  <button
                    key={lbl as string}
                    type="button"
                    className={"segmented-btn" + (ratings.newFactor === v ? " active" : "")}
                    disabled={!ratings.useRatings}
                    onClick={() => setRatingsCfg({ ...ratings, newFactor: v as number })}
                  >
                    {lbl}
                  </button>
                ))}
              </div>
              <input
                type="number" min={0} max={20} step={0.1}
                value={ratings.newFactor}
                aria-label="Custom new-songs weight"
                disabled={!ratings.useRatings}
                onChange={(e) => setRatingsCfg({ ...ratings, newFactor: Math.max(0, Number(e.target.value) || 0) })}
                style={{ width: 78, fontFamily: "var(--mono)", textAlign: "center" }}
              />
            </div>
          </div>
          {ratingsPreview && (
            <div className="field-row">
              {fieldLabel("Expected share of picks", "A live preview under the values above (not yet saved) — the share each level would get, and how many tracks currently sit at that level.")}
              <div style={{ display: "flex", flexDirection: "column", gap: 4, maxWidth: 420 }}>
                {(["5", "4", "3", "2", "1", "unrated", "new"] as const).map((k) => {
                  const share = ratingsPreview.share[k] ?? 0;
                  const count = ratingsPreview.counts[k] ?? 0;
                  return (
                    <div key={k} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span style={{ width: 52, fontSize: 11, color: "var(--muted)", textTransform: "capitalize" }}>
                        {k === "unrated" ? "Unrated" : k === "new" ? "New" : `${k}★`}
                      </span>
                      <div style={{ flex: 1, height: 8, background: "var(--border)", borderRadius: 6, overflow: "hidden" }}>
                        <div style={{ width: `${Math.round(share * 100)}%`, height: "100%", background: "var(--accent)" }} />
                      </div>
                      <span style={{ width: 84, textAlign: "right", fontSize: 11, fontFamily: "var(--mono)" }}>
                        {(share * 100).toFixed(1)}% · {count}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
          <div style={{ marginTop: 14, display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <SaveButton state={ratingsSaved} label="Save Ratings Settings" />
            <button
              type="button" className="btn btn-ghost btn-sm"
              onClick={() => setRatingsCfg(RATING_DEFAULTS)}
            >
              Reset to defaults
            </button>
          </div>
        </form>
      </div>
    </>
  );
}

export function PlaybackCard() {
  const qc = useQueryClient();
  const { cfg } = useSettings();
  const saveSection = useSaveSection();
  const [playback, setPlayback] = useState(3);
  const [loud, setLoud] = useState({ normalize: true, target: -14, measure: true });
  const [preload, setPreload] = useState({ ahead: 5, cacheMb: 500 });
  const [playSaved, setPlaySaved] = useState<Saved>("");
  useEffect(() => {
    if (!cfg) return;
    const { n, b } = readers(cfg);
    setPlayback(n("playback_buffer", 3));
    setLoud({ normalize: b("normalize_playback", true), target: n("loudness_target_lufs", -14), measure: b("measure_loudness", true) });
    setPreload({ ahead: n("preload_ahead", 5), cacheMb: n("preload_cache_mb", 500) });
  }, [cfg]);

  // This browser's offline audio cache (Cache API is per-device — this row
  // reports and clears only the device you're on).
  const [offlineUse, setOfflineUse] = useState(() => cacheStats());
  // Heal the size index against what the browser actually still stores (it may
  // have evicted under pressure) so the readout never overreports.
  useEffect(() => {
    if (offlineSupported()) void reconcileIndex().then(() => setOfflineUse(cacheStats()));
  }, []);

  // Loudness back-fill: poll while it runs. `remaining` is queried live, so it
  // also tells you how much is left before you ever press start.
  const loudnessFillQ = useQuery({
    queryKey: ["loudness-fill"],
    queryFn: () => api.get<LoudnessFillStatus>("/api/loudness/fill/status"),
    refetchInterval: (q) => (q.state.data?.running ? 1500 : false),
  });
  async function startLoudnessFill() {
    await api.post("/api/loudness/fill/start", {});
    qc.invalidateQueries({ queryKey: ["loudness-fill"] });
  }
  async function cancelLoudnessFill() {
    await api.post("/api/loudness/fill/cancel", {});
    qc.invalidateQueries({ queryKey: ["loudness-fill"] });
  }

  return (
    <>
      {/* Playback */}
      <div id="sec-playback" className="settings-card card">
        <div className="settings-card-header">
          <h2>Playback</h2>
          <p>Audio buffering and volume levelling for the built-in player.</p>
        </div>
        <form onSubmit={(e) => { e.preventDefault(); saveSection("/api/settings/playback", { playback_buffer: playback, normalize_playback: loud.normalize, loudness_target_lufs: loud.target, measure_loudness: loud.measure, preload_ahead: preload.ahead, preload_cache_mb: preload.cacheMb }, setPlaySaved); }}>
          <div className="field-row">
            {fieldLabel("Buffer before play", "Seconds of audio to buffer before starting playback. Set to 0 to play immediately. Increase if you hear stuttering on slow storage (NAS).")}
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input type="number" min={0} max={30} step={0.5} value={playback} onChange={(e) => setPlayback(+e.target.value)} style={{ width: 78, fontFamily: "var(--mono)", textAlign: "center" }} />
              <span style={{ color: "var(--muted)", fontSize: 13 }}>s</span>
            </div>
          </div>
          <div className="field-row" style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
            {fieldLabel("Level volume between tracks", "Bring every track to a consistent loudness so a hot master doesn't jump out mid-run. Tracks louder than the target are turned down; quieter ones play as-is (the player can only attenuate). Tracks with no measurement play untouched.")}
            <Toggle on={loud.normalize} onChange={(v) => setLoud({ ...loud, normalize: v })} label="Level volume" />
          </div>
          <div className="field-row">
            {fieldLabel("Target loudness", "The level tracks are brought down to, in LUFS. -14 matches what streaming services use. Lower means quieter and more tracks get attenuated; higher leaves more alone.")}
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input
                type="number" min={-30} max={-5} step={0.5} value={loud.target}
                disabled={!loud.normalize}
                onChange={(e) => setLoud({ ...loud, target: Math.max(-30, Math.min(-5, +e.target.value)) })}
                style={{ width: 78, fontFamily: "var(--mono)", textAlign: "center" }}
              />
              <span style={{ color: "var(--muted)", fontSize: 13 }}>LUFS</span>
            </div>
          </div>
          <div className="field-row">
            {fieldLabel("Measure during scans", "Measure each track's loudness while it's being analyzed for BPM. Files that already carry a ReplayGain tag are read instead of measured, so most tagged libraries cost nothing extra.")}
            <Toggle on={loud.measure} onChange={(v) => setLoud({ ...loud, measure: v })} label="Measure loudness" />
          </div>
          <div className="field-row" style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
            {fieldLabel("Preload ahead", "How many upcoming queue tracks the player downloads in full while playing, so short network drops don't stop the music. 0 turns the look-ahead off. Preloading needs HTTPS (or localhost) — over plain HTTP the player just streams.")}
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input type="number" min={0} max={20} step={1} value={preload.ahead}
                     onChange={(e) => setPreload({ ...preload, ahead: Math.max(0, Math.min(20, Math.round(+e.target.value))) })}
                     style={{ width: 78, fontFamily: "var(--mono)", textAlign: "center" }} />
              <span style={{ color: "var(--muted)", fontSize: 13 }}>tracks</span>
            </div>
          </div>
          <div className="field-row">
            {fieldLabel("Offline cache cap", "How much audio each device may keep for offline playback (look-ahead plus the Run page's Prepare-offline downloads). Past the cap, the tracks untouched longest are dropped first.")}
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input type="number" min={50} max={5000} step={50} value={preload.cacheMb}
                     onChange={(e) => setPreload({ ...preload, cacheMb: Math.max(50, Math.min(5000, Math.round(+e.target.value))) })}
                     style={{ width: 78, fontFamily: "var(--mono)", textAlign: "center" }} />
              <span style={{ color: "var(--muted)", fontSize: 13 }}>MB</span>
            </div>
          </div>
          {offlineSupported() && (
            <div className="field-row">
              {fieldLabel("This device's cache", "What's currently stored on this device. The cache is per-device — clearing it here doesn't touch your other devices.")}
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span style={{ fontSize: 12, color: "var(--muted)", fontFamily: "var(--mono)" }}>
                  {offlineUse.tracks} track{offlineUse.tracks === 1 ? "" : "s"} · {fmtBytes(offlineUse.bytes)}
                </span>
                <button className="btn btn-ghost btn-sm" type="button"
                        disabled={offlineUse.tracks === 0}
                        onClick={() => { void clearOffline().then(() => setOfflineUse(cacheStats())); }}>
                  Clear
                </button>
              </div>
            </div>
          )}
          <div style={{ marginTop: 14 }}>
            <SaveButton state={playSaved} label="Save Playback Settings" />
          </div>
        </form>
        <div style={{ borderTop: "1px solid var(--border)", marginTop: 16, paddingTop: 14 }}>
          <p style={{ fontSize: 12, color: "var(--muted)", marginBottom: 10, lineHeight: 1.5, maxWidth: 500 }}>
            Tracks scanned before levelling existed have no measurement yet. This measures them
            in the background — one file at a time, so it won't starve a running scan. Existing
            ReplayGain tags are reused where present.
          </p>
          {(() => {
            const f = loudnessFillQ.data;
            return (
              <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                <button className="btn btn-primary btn-sm" type="button"
                        disabled={f?.running || f?.remaining === 0} onClick={startLoudnessFill}>
                  {f?.running ? "Measuring…" : "Measure missing loudness"}
                </button>
                {f?.running && (
                  <button className="btn btn-ghost btn-sm" type="button" onClick={cancelLoudnessFill}>Cancel</button>
                )}
                {f && (f.running || f.total > 0) && (
                  <span style={{ fontSize: 12, color: "var(--muted)", fontFamily: "var(--mono)" }}>
                    {f.done}/{f.total} done · {f.measured} measured · {f.tagged} from tags · {f.failed} failed
                  </span>
                )}
                {f && !f.running && (
                  <span style={{ fontSize: 12, color: "var(--muted)" }}>
                    {f.remaining === 0 ? "Every track is measured." : `${f.remaining} track${f.remaining === 1 ? "" : "s"} still unmeasured.`}
                  </span>
                )}
              </div>
            );
          })()}
        </div>
      </div>
    </>
  );
}

export function RunCard() {
  const { cfg } = useSettings();
  const saveSection = useSaveSection();
  const [run, setRun] = useState({
    presets: PRESET_DEFAULTS,
    octave: true,
    queueSize: 20, stretchLimit: 15, preloadTracks: 10,
  });
  const [runSaved, setRunSaved] = useState<Saved>("");
  useEffect(() => {
    if (!cfg) return;
    const { n, b } = readers(cfg);
    setRun({
      // Accept both shapes: {name,bpm} dicts and the legacy plain-number list.
      presets: [0, 1, 2, 3].map((i) => {
        const raw = Array.isArray(cfg.run_presets) ? (cfg.run_presets as unknown[])[i] : undefined;
        if (raw && typeof raw === "object") {
          const p = raw as { name?: unknown; bpm?: unknown };
          return { name: String(p.name ?? PRESET_DEFAULTS[i].name), bpm: Number(p.bpm ?? PRESET_DEFAULTS[i].bpm) };
        }
        if (typeof raw === "number") return { name: PRESET_DEFAULTS[i].name, bpm: raw };
        return PRESET_DEFAULTS[i];
      }),
      octave: b("run_octave_fold", true),
      queueSize: n("run_queue_size", 20),
      stretchLimit: n("run_stretch_limit_pct", 15),
      preloadTracks: n("run_preload_tracks", 10),
    });
  }, [cfg]);

  return (
    <>
      {/* Run mode */}
      <div id="sec-run" className="settings-card card">
        <div className="settings-card-header">
          <h2>Run Mode</h2>
          <p>
            Tempo-locked playback for cadence running — configure the <code>/run</code> page's presets
            and how the queue is matched. Which tracks get picked (ratings, new-song weight) now lives
            in <a href="#sec-ratings">Ratings &amp; picking</a> below.
          </p>
        </div>
        <form onSubmit={(e) => {
          e.preventDefault();
          saveSection("/api/settings/run", {
            run_presets: run.presets,
            run_octave_fold: run.octave,
            run_queue_size: run.queueSize,
            run_stretch_limit_pct: run.stretchLimit,
            run_preload_tracks: run.preloadTracks,
          }, setRunSaved);
        }}>
          <div className="field-row">
            {fieldLabel("BPM presets", "Four named one-tap cadence targets shown on the Run page — e.g. your warmup, easy, steady and tempo cadences.")}
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {run.presets.map((p, i) => (
                <div key={i} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  <input type="text" maxLength={20} value={p.name} placeholder="Name" aria-label={`Preset ${i + 1} name`}
                         onChange={(e) => setRun({ ...run, presets: run.presets.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)) })}
                         style={{ width: 86, fontSize: 12, textAlign: "center" }} />
                  <input type="number" min={30} max={300} step={1} value={p.bpm} aria-label={`Preset ${i + 1} BPM`}
                         onChange={(e) => setRun({ ...run, presets: run.presets.map((x, j) => (j === i ? { ...x, bpm: +e.target.value } : x)) })}
                         style={{ width: 86, fontFamily: "var(--mono)", textAlign: "center" }} />
                </div>
              ))}
            </div>
          </div>
          <div className="field-row">
            {fieldLabel("Octave matching", "Count half- and double-time tracks as matches: at a 150 cadence a 75 BPM song plays at native speed and you step on every beat. Off = only tracks whose actual BPM is near the target.")}
            <Toggle on={run.octave} onChange={(v) => setRun({ ...run, octave: v })} label="Octave matching" />
          </div>
          <div className="field-row">
            {fieldLabel("Queue size", "How many tracks a run queue preloads.")}
            <input type="number" min={1} max={200} step={1} value={run.queueSize}
                   onChange={(e) => setRun({ ...run, queueSize: +e.target.value })}
                   style={{ width: 78, fontFamily: "var(--mono)", textAlign: "center" }} />
          </div>
          <div className="field-row">
            {fieldLabel("Prepare offline size", "How many tracks a per-preset Prepare-offline download grabs on the Run page — enough cadence-matched music to cross the places where the network dies.")}
            <input type="number" min={1} max={50} step={1} value={run.preloadTracks}
                   onChange={(e) => setRun({ ...run, preloadTracks: +e.target.value })}
                   style={{ width: 78, fontFamily: "var(--mono)", textAlign: "center" }} />
          </div>
          <div className="field-row">
            {fieldLabel("Max stretch", "How far (in %) a track may be sped up or slowed down to reach your cadence. Tracks that can't get there within this limit aren't queued. Browser time-stretching starts to sound artificial past ~15%.")}
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input type="number" min={1} max={50} step={1} value={run.stretchLimit}
                     onChange={(e) => setRun({ ...run, stretchLimit: +e.target.value })}
                     style={{ width: 78, fontFamily: "var(--mono)", textAlign: "center" }} />
              <span style={{ color: "var(--muted)", fontSize: 13 }}>%</span>
            </div>
          </div>
          <div style={{ marginTop: 14 }}>
            <SaveButton state={runSaved} label="Save Run Settings" />
          </div>
        </form>
      </div>
    </>
  );
}
