export type Theme = "dark" | "light";

const KEY = "bpm-theme";

export function initialTheme(): Theme {
  const saved = localStorage.getItem(KEY);
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(KEY, theme);
  // Canvas-drawn UI (the waveform) resolves CSS vars to concrete colors, so it
  // must repaint when the theme swaps its palette. See hooks/useWaveform.ts.
  window.dispatchEvent(new CustomEvent("bpm:appearance"));
}
