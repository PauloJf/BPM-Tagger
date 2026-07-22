import { useCallback, useEffect, useRef, useState } from "react";

/** Shared maximize + drag-to-resize behaviour for the player bar's popover
 *  drawers (queue, lyrics). Both are anchored to the bottom-right above the
 *  player bar, so the grab handle lives on the *top-left* corner: dragging it
 *  up/left grows the drawer, down/right shrinks it. The chosen size persists
 *  per-browser in localStorage (mirrors lib/theme.ts).
 *
 *  On small screens (≤700px) the resize handle is suppressed and "maximize"
 *  becomes a full-height bottom sheet — both handled in CSS via the `.maximized`
 *  class; this hook just reports `small` so the caller can skip the handle. */

export interface DrawerSize {
  width: number;
  height: number;
}

interface Options {
  /** localStorage key, e.g. "bpm-queue-size". */
  key: string;
  minWidth?: number;
  minHeight?: number;
}

function loadSize(key: string): DrawerSize | null {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const p = JSON.parse(raw);
    if (typeof p?.width === "number" && typeof p?.height === "number") {
      return { width: p.width, height: p.height };
    }
  } catch {
    /* ignore malformed value */
  }
  return null;
}

export function useResizableDrawer({ key, minWidth = 280, minHeight = 160 }: Options) {
  const [maximized, setMaximized] = useState(false);
  const [size, setSize] = useState<DrawerSize | null>(() => loadSize(key));

  const [small, setSmall] = useState(
    () => window.matchMedia?.("(max-width: 700px)").matches ?? false,
  );
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 700px)");
    const on = () => setSmall(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);

  const toggleMaximized = useCallback(() => setMaximized((m) => !m), []);

  // Escape restores from the maximized state.
  useEffect(() => {
    if (!maximized) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMaximized(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [maximized]);

  const drag = useRef<{ x: number; y: number; w: number; h: number } | null>(null);

  const onPointerMove = useCallback(
    (e: PointerEvent) => {
      const d = drag.current;
      if (!d) return;
      const maxW = window.innerWidth - 24;
      const maxH = Math.round(window.innerHeight * 0.85);
      const width = Math.max(minWidth, Math.min(maxW, d.w + (d.x - e.clientX)));
      const height = Math.max(minHeight, Math.min(maxH, d.h + (d.y - e.clientY)));
      setSize({ width, height });
    },
    [minWidth, minHeight],
  );

  const onPointerUp = useCallback(() => {
    drag.current = null;
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", onPointerUp);
    setSize((s) => {
      if (s) {
        try {
          localStorage.setItem(key, JSON.stringify(s));
        } catch {
          /* quota / private mode — size just won't persist */
        }
      }
      return s;
    });
  }, [key, onPointerMove]);

  const startResize = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      const el = (e.currentTarget as HTMLElement).closest("[data-drawer]") as HTMLElement | null;
      const rect = el?.getBoundingClientRect();
      drag.current = {
        x: e.clientX,
        y: e.clientY,
        w: rect?.width ?? minWidth,
        h: rect?.height ?? minHeight,
      };
      window.addEventListener("pointermove", onPointerMove);
      window.addEventListener("pointerup", onPointerUp);
    },
    [minWidth, minHeight, onPointerMove, onPointerUp],
  );

  // Drop listeners if the drawer unmounts mid-drag.
  useEffect(
    () => () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
    },
    [onPointerMove, onPointerUp],
  );

  // Only apply an explicit size when the user has resized and we're neither
  // maximized nor on a small screen (both of those are CSS-driven).
  const style: React.CSSProperties =
    !maximized && !small && size
      ? { width: size.width, height: size.height, maxHeight: "none" }
      : {};

  return { maximized, toggleMaximized, startResize, style, small };
}
