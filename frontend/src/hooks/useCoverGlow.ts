import { useEffect, useState } from "react";

// path -> "r, g, b" (average cover color) | null (no cover / extraction failed).
// Module-level cache: covers rarely change mid-session, so a revisited track
// shouldn't redecode its image every time.
const colorCache = new Map<string, string | null>();

// Downsampled onto a tiny canvas — the browser's own resize smoothing gives a
// reasonable "vibe" color for a fraction of the cost of a real palette
// extraction (k-means/histogram), which would be overkill for an ambient glow.
function averageColor(img: HTMLImageElement): string | null {
  const size = 12;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.drawImage(img, 0, 0, size, size);
  let data: Uint8ClampedArray;
  try {
    data = ctx.getImageData(0, 0, size, size).data;
  } catch {
    return null; // tainted canvas — shouldn't happen, cover art is same-origin
  }
  let r = 0, g = 0, b = 0, n = 0;
  for (let i = 0; i < data.length; i += 4) {
    if (data[i + 3] < 32) continue; // skip near-transparent pixels
    r += data[i]; g += data[i + 1]; b += data[i + 2]; n++;
  }
  return n ? `${Math.round(r / n)}, ${Math.round(g / n)}, ${Math.round(b / n)}` : null;
}

/** Average color of a track's cover art, cached per path. Resolves to null
 *  while loading, and stays null for tracks with no cover art (or a decode
 *  failure) — callers should fall back to no glow in that case. */
function useCoverColor(path: string | null): string | null {
  const [color, setColor] = useState<string | null>(() => (path ? colorCache.get(path) ?? null : null));

  useEffect(() => {
    if (!path) { setColor(null); return; }
    const cached = colorCache.get(path);
    if (cached !== undefined) { setColor(cached); return; }

    let cancelled = false;
    const img = new Image();
    img.onload = () => {
      if (cancelled) return;
      const c = averageColor(img);
      colorCache.set(path, c);
      setColor(c);
    };
    img.onerror = () => {
      if (cancelled) return;
      colorCache.set(path, null);
      setColor(null);
    };
    img.src = `/api/track/cover?path=${encodeURIComponent(path)}`;
    return () => { cancelled = true; };
  }, [path]);

  return color;
}

/** A soft ambient glow behind the Run page, tinted with the playing track's
 *  cover color. Crossfades between tracks (fade to flat, swap color, fade
 *  back in) instead of snapping straight to the new color — a hard cut on
 *  every track change would be distracting in a full-screen, ambient page. */
export function useCoverGlow(path: string | null): { background: string; opacity: number } {
  const color = useCoverColor(path);
  const [shown, setShown] = useState<string | null>(color);
  const [visible, setVisible] = useState(!!color);

  useEffect(() => {
    if (color === shown) return;
    setVisible(false);
    // Matches the CSS transition duration the caller applies to `opacity`.
    const t = setTimeout(() => {
      setShown(color);
      setVisible(!!color);
    }, 450);
    return () => clearTimeout(t);
    // `shown` is intentionally left out of the deps — it's only ever set from
    // within this effect, so including it would refire immediately after
    // every swap and skip the fade.
  }, [color]);

  return {
    background: shown
      ? `radial-gradient(ellipse 90% 55% at 50% -10%, rgba(${shown}, 0.4), transparent 70%)`
      : "none",
    opacity: visible ? 1 : 0,
  };
}
