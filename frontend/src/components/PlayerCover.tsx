import { useEffect, useState } from "react";

/** Hold-then-swap cover art for the full-screen players (Run, Listen).
 *
 *  Extracted from Run.tsx (where it was RunCover) so Listen's mobile layout can
 *  reuse the same height-driven sizing. Two modes:
 *  - width-driven (`coverSize`, desktop): width:min(coverSize,100%), centered
 *    via margin:auto. Resolves fine because the parent is a full-width
 *    flex/block, not a shrink-to-fit inline box — so it never collapses (the
 *    2.6.6 bug) and the aspect-ratio always wins, while the click target and
 *    any overlays share the exact cover footprint.
 *  - height-driven (`fillHeight`, the mobile fill layout): absolutely fills the
 *    (position:relative) cover slot. top/bottom:0 gives a definite height that
 *    iOS Safari resolves reliably — a plain height:100% + aspect-ratio here
 *    collapses to zero width on iOS (the flex-basis:0 slot's height is treated
 *    as indefinite for an in-flow percentage child), which blanked the cover on
 *    iPhone while Android rendered it fine.
 *
 *  `shown` is the path whose art is currently painted; it only advances to the
 *  new `path` after that image has finished loading (or errored) — no flash of
 *  empty box between tracks. */
export default function PlayerCover({ path, coverSize, fillHeight, onClick, onMouseEnter, onMouseLeave, ariaLabel, ariaPressed, title, children }: {
  path: string;
  coverSize?: string;    // width-driven square (desktop cockpit)
  fillHeight?: boolean;  // height-driven square — fills the flexible mobile cover slot
  onClick?: () => void;
  onMouseEnter?: () => void;
  onMouseLeave?: () => void;
  ariaLabel?: string;
  ariaPressed?: boolean;
  title?: string;
  children?: React.ReactNode;   // absolute overlays (pop-out hint, source chip)
}) {
  const src = (p: string) => `/api/track/cover?path=${encodeURIComponent(p)}`;
  const [shown, setShown] = useState(path);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (path === shown) return;
    let cancelled = false;
    const img = new Image();
    img.onload = () => { if (!cancelled) { setShown(path); setFailed(false); } };
    img.onerror = () => { if (!cancelled) { setShown(path); setFailed(true); } };
    img.src = src(path);
    return () => { cancelled = true; };
  }, [path, shown]);

  const box: React.CSSProperties = fillHeight
    ? {
        position: "absolute", top: 0, bottom: 0, left: "50%", transform: "translateX(-50%)",
        width: "auto", maxWidth: "100%", aspectRatio: "1 / 1",
        borderRadius: 20, overflow: "hidden",
        boxShadow: "0 18px 50px -18px rgba(0,0,0,0.6)", background: "var(--surface)",
        border: "none", padding: 0, cursor: onClick ? "pointer" : "default",
      }
    : {
        position: "relative", display: "block", width: `min(${coverSize}, 100%)`,
        aspectRatio: "1 / 1", margin: "0 auto", borderRadius: 20, overflow: "hidden",
        boxShadow: "0 18px 50px -18px rgba(0,0,0,0.6)", background: "var(--surface)",
        border: "none", padding: 0, cursor: onClick ? "pointer" : "default",
      };
  const inner = (
    <>
      {failed ? (
        <span className="art-thumb" style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", fontSize: 48 }} aria-hidden>♪</span>
      ) : (
        <img src={src(shown)} alt="" onError={() => setFailed(true)} style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover", display: "block" }} />
      )}
      {children}
    </>
  );
  return onClick ? (
    <button type="button" style={box} onClick={onClick} onMouseEnter={onMouseEnter} onMouseLeave={onMouseLeave} aria-label={ariaLabel} aria-pressed={ariaPressed} title={title}>
      {inner}
    </button>
  ) : (
    <div style={box} title={title}>{inner}</div>
  );
}
