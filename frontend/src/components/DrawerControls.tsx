/** Shared chrome for the resizable player drawers (queue, lyrics): a maximize/
 *  restore toggle for the header button group, the top-left grab handle, and a
 *  text-size stepper (with a persisted per-drawer preference). */

import { useState } from "react";

// Drawer text-size preference, persisted per-browser (mirrors lib/theme.ts).
// Four steps so there's an "even larger" option beyond the old S/M/L.
export type DrawerFont = "s" | "m" | "l" | "xl";
const FONTS: readonly DrawerFont[] = ["s", "m", "l", "xl"] as const;
const FONT_TITLE: Record<DrawerFont, string> = {
  s: "Small text", m: "Medium text", l: "Large text", xl: "Extra-large text",
};

/** Persisted text-size state for one drawer. `key` is its localStorage slot
 *  (e.g. "bpm-lyrics-font", "bpm-queue-font"); defaults to medium. */
export function useDrawerFont(key: string): [DrawerFont, (f: DrawerFont) => void] {
  const [font, setFont] = useState<DrawerFont>(() => {
    try {
      const v = localStorage.getItem(key);
      if (v === "s" || v === "m" || v === "l" || v === "xl") return v;
    } catch { /* private mode */ }
    return "m";
  });
  const change = (f: DrawerFont) => {
    setFont(f);
    try { localStorage.setItem(key, f); } catch { /* private mode */ }
  };
  return [font, change];
}

export function FontStepper({ font, onChange, label = "Text size" }: {
  font: DrawerFont;
  onChange: (f: DrawerFont) => void;
  label?: string;
}) {
  return (
    <span className="drawer-font-stepper" role="group" aria-label={label}>
      {FONTS.map((f) => (
        <button
          key={f}
          className={"btn btn-bare btn-sm" + (font === f ? " active" : "")}
          onClick={() => onChange(f)}
          aria-pressed={font === f}
          title={FONT_TITLE[f]}
        >
          {f.toUpperCase()}
        </button>
      ))}
    </span>
  );
}

export function MaximizeButton({ maximized, onToggle }: {
  maximized: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      className="btn btn-bare btn-sm"
      onClick={onToggle}
      aria-label={maximized ? "Restore drawer size" : "Maximize drawer"}
      aria-pressed={maximized}
      title={maximized ? "Restore" : "Maximize"}
    >
      {maximized ? (
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M8 10H4V6M16 10h4V6M8 14H4v4M16 14h4v4" />
        </svg>
      ) : (
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M8 3H3v5M21 8V3h-5M3 16v5h5M16 21h5v-5" />
        </svg>
      )}
    </button>
  );
}

export function ResizeHandle({ onPointerDown }: {
  onPointerDown: (e: React.PointerEvent) => void;
}) {
  return (
    <div
      className="drawer-resize-handle"
      onPointerDown={onPointerDown}
      role="separator"
      aria-label="Resize drawer"
      title="Drag to resize"
    >
      <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
        <path d="M2 8 L8 2 M2 5 L5 2" />
      </svg>
    </div>
  );
}
