// Custom accent color. The whole design system derives every accent shade from
// a single OKLCH hue angle (`--accent-h` in design-system.css), so recoloring
// the UI — nav/login logo tiles, buttons, badges, focus rings, progress fills,
// the login glow + animated bars — is just a matter of overriding that one
// variable. Lightness and chroma stay fixed, so any hue stays perceptually
// balanced and contrast-safe in both light and dark themes.
//
// Persisted per-browser in localStorage (mirrors lib/theme.ts) so the accent is
// applied before React mounts — including on the pre-auth login screen, which a
// server-side store could not reach without a violet flash.

export type AccentHue = number;

const KEY = "bpm-accent";

/** The stock violet defined in design-system.css. */
export const DEFAULT_ACCENT_HUE = 290;

export interface AccentPreset {
  name: string;
  hue: number;
}

export const ACCENT_PRESETS: AccentPreset[] = [
  { name: "Violet", hue: 290 },
  { name: "Indigo", hue: 275 },
  { name: "Blue", hue: 255 },
  { name: "Cyan", hue: 220 },
  { name: "Teal", hue: 185 },
  { name: "Green", hue: 150 },
  { name: "Amber", hue: 85 },
  { name: "Orange", hue: 55 },
  { name: "Red", hue: 25 },
  { name: "Rose", hue: 5 },
  { name: "Pink", hue: 350 },
  { name: "Magenta", hue: 320 },
];

/** Swatch preview color — mirrors the `--accent` formula in design-system.css. */
export function accentSwatch(hue: number): string {
  return `oklch(0.665 0.190 ${hue})`;
}

export function initialAccentHue(): AccentHue {
  const raw = localStorage.getItem(KEY);
  if (raw === null) return DEFAULT_ACCENT_HUE;
  const n = Number(raw);
  return Number.isFinite(n) && n >= 0 && n <= 360 ? n : DEFAULT_ACCENT_HUE;
}

export function applyAccentHue(hue: AccentHue) {
  const clamped = Math.max(0, Math.min(360, Math.round(hue)));
  document.documentElement.style.setProperty("--accent-h", String(clamped));
  localStorage.setItem(KEY, String(clamped));
  // Canvas-drawn UI (the waveform) can't read CSS vars live — tell it to
  // re-resolve its colors and repaint. Harmless before React mounts (no
  // listeners yet). See hooks/useWaveform.ts.
  window.dispatchEvent(new CustomEvent("bpm:appearance"));
}
