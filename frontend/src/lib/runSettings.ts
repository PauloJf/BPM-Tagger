import type { SettingsMap } from "./types";

/** Where the Run page remembers its choices (shared with the Listen → Run dialog,
 *  so a run started from either lands on the same target and source). */
export const TARGET_KEY = "bpm.run.target";
export const SOURCE_KEY = "bpm.run.source";   // "library" | "mine" | "pl:<id>"

export const PRESET_DEFAULTS = [
  { name: "Warmup", bpm: 120 }, { name: "Easy", bpm: 155 },
  { name: "Steady", bpm: 165 }, { name: "Tempo", bpm: 175 },
];

export function clampTarget(v: number): number {
  return Math.max(30, Math.min(300, Math.round(v)));
}

export interface RunPreset { name: string; bpm: number }

/** The four run presets from settings. Accepts both shapes: {name,bpm} dicts and
 *  the legacy plain-number list. */
export function parseRunPresets(cfg: SettingsMap | undefined): RunPreset[] {
  return [0, 1, 2, 3].map((i) => {
    const raw = Array.isArray(cfg?.run_presets) ? (cfg!.run_presets as unknown[])[i] : undefined;
    if (raw && typeof raw === "object") {
      const p = raw as { name?: unknown; bpm?: unknown };
      return { name: String(p.name ?? PRESET_DEFAULTS[i].name), bpm: Number(p.bpm ?? PRESET_DEFAULTS[i].bpm) };
    }
    if (typeof raw === "number") return { name: PRESET_DEFAULTS[i].name, bpm: raw };
    return PRESET_DEFAULTS[i];
  });
}

export function runOctave(cfg: SettingsMap | undefined): boolean {
  return cfg?.run_octave_fold == null ? true : Boolean(cfg.run_octave_fold);
}

export function runStretchLimitPct(cfg: SettingsMap | undefined): number {
  return cfg?.run_stretch_limit_pct == null ? 15 : Number(cfg.run_stretch_limit_pct);
}

/** The target the Run page would open with: the remembered one, else 155. */
export function rememberedTarget(): number {
  try {
    const saved = Number(localStorage.getItem(TARGET_KEY));
    return saved >= 30 && saved <= 300 ? saved : 155;
  } catch { return 155; }
}

/** The remembered run source as the player stores it (a playlist id, "mine", or
 *  null for the whole library) and as the queue endpoint's `&playlist=` scope. */
export function rememberedRunSource(): { source: number | "mine" | null; scope: string } {
  let s = "library";
  try { s = localStorage.getItem(SOURCE_KEY) || "library"; } catch { /* no storage */ }
  if (s === "mine") return { source: "mine", scope: "&playlist=mine" };
  if (s.startsWith("pl:") && Number(s.slice(3)) > 0) {
    const id = Number(s.slice(3));
    return { source: id, scope: `&playlist=${id}` };
  }
  return { source: null, scope: "" };
}
