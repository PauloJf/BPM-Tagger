import type { CSSProperties, ReactNode, RefObject } from "react";
import { createPortal } from "react-dom";
import { fmtTime } from "../hooks/useAudioTime";
import { LyricsPanel } from "./LyricsPanel";

/** The pieces Run and Listen share: the same cover glow, lyrics drawer, waveform
 *  strip, transport buttons and queue card, drawn once. Each page still owns what
 *  makes it itself (Run's cadence cockpit, Listen's source and radio controls) and
 *  passes the few sizes that differ between them as props. */

/** A round transport button's base style at a given diameter. */
export function ctlButtonStyle(size: number): CSSProperties {
  return {
    width: size, height: size, borderRadius: 999, display: "inline-flex", alignItems: "center",
    justifyContent: "center", background: "var(--surface)", border: "1px solid var(--border)",
    color: "var(--text)", cursor: "pointer",
  };
}

/** Ambient glow tinted from the cover art, sitting behind the nav and content
 *  (z-index -1 paints above the plain body background, below everything with
 *  default stacking). Portaled straight to <body>: `.container` briefly carries a
 *  transform during its page-enter animation, and a transformed ancestor becomes
 *  the containing block for a `position: fixed` child, which would misplace it
 *  relative to `.container` instead of the viewport. */
export function GlowLayer({ glow }: { glow: { background: string; opacity: number } }) {
  return createPortal(
    <div
      aria-hidden
      style={{
        position: "fixed", inset: 0, zIndex: -1, pointerEvents: "none",
        transition: "opacity 0.45s ease",
        background: glow.background, opacity: glow.opacity,
      }}
    />,
    document.body,
  );
}

/** The lyrics drawer, anchored to a zero-height strip at the bottom of the
 *  viewport: these pages have no player bar to hang it off. */
export function LyricsDrawer({ path, audioRef, onClose }: {
  path: string;
  audioRef: RefObject<HTMLAudioElement>;
  onClose: () => void;
}) {
  return (
    <div style={{ position: "fixed", left: 0, right: 0, bottom: 0, zIndex: 61 }}>
      <LyricsPanel path={path} audioRef={audioRef} onClose={onClose} />
    </div>
  );
}

/** The seekable waveform with elapsed and remaining time under it. The canvas is
 *  drawn into by useWaveform through `canvasRef`. */
export function WaveformStrip({ canvasRef, time, dur }: {
  canvasRef: RefObject<HTMLCanvasElement>;
  time: number;
  dur: number;
}) {
  return (
    <>
      <canvas ref={canvasRef} style={{ width: "100%", height: 44, display: "block", cursor: "pointer" }} />
      <div style={{ display: "flex", justifyContent: "space-between", fontFamily: "var(--mono)", fontSize: 12, color: "var(--muted)", marginTop: 4 }}>
        <span>{fmtTime(time)}</span>
        <span>-{fmtTime(Math.max(0, dur - time))}</span>
      </div>
    </>
  );
}

/** Previous, play/pause and next. `sideSize` and `playSize` are the button
 *  diameters and `iconSize` the play/pause glyph: Run and Listen differ slightly
 *  and the difference is theirs to keep. */
export function TransportButtons({ playing, onPrev, onToggle, onNext, sideSize, playSize, iconSize }: {
  playing: boolean;
  onPrev: () => void;
  onToggle: () => void;
  onNext: () => void;
  sideSize: number;
  playSize: number;
  iconSize: number;
}) {
  const ctl = ctlButtonStyle(sideSize);
  return (
    <>
      <button style={ctl} onClick={onPrev} aria-label="Previous" title="Previous">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M6 5v14h2V5H6zm3 7l11 7V5l-11 7z" /></svg>
      </button>
      <button
        style={{ ...ctl, width: playSize, height: playSize, background: "var(--accent)", border: "none", boxShadow: "0 12px 40px -10px var(--accent)" }}
        onClick={onToggle}
        aria-label={playing ? "Pause" : "Play"}
      >
        {playing ? (
          <svg width={iconSize} height={iconSize} viewBox="0 0 24 24" fill="white"><rect x="6" y="4" width="4" height="16" rx="1" /><rect x="14" y="4" width="4" height="16" rx="1" /></svg>
        ) : (
          <svg width={iconSize} height={iconSize} viewBox="0 0 24 24" fill="white" style={{ marginLeft: 3 }}><polygon points="6,4 20,12 6,20" /></svg>
        )}
      </button>
      <button style={ctl} onClick={onNext} aria-label="Next" title="Next">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M16 5v14h2V5h-2zM4 19l11-7L4 5v14z" /></svg>
      </button>
    </>
  );
}

/** The queue card: a header, optional rows between the header and the list (a
 *  source picker, a similar-tracks panel), and the scrolling list.
 *
 *  Both hosts hand it a bounded box and the list scrolls inside it. `column`
 *  (the desktop side column) absolutely fills its container, so the queue never
 *  drives the row height and its bottom always lines up with the transport.
 *  Otherwise (the mobile Queue tab) a short queue sizes to its content and a long
 *  one shrinks (flexShrink, bounded by minHeight:0) to the space the fixed-height
 *  mobile column has left; that shrink only bites because the Queue tab hard-caps
 *  the column height with the `run-queue-open` class, since under min-height alone
 *  there is no negative free space and the page would scroll instead. */
export function QueueCard({ column, bottomGap, header, between, listRef, listTestId, children }: {
  column: boolean;
  /** Space under the card in the mobile (non-column) layout. */
  bottomGap: number;
  header: ReactNode;
  between?: ReactNode;
  listRef?: RefObject<HTMLDivElement>;
  listTestId?: string;
  children: ReactNode;
}) {
  const card: CSSProperties = column
    ? { padding: 0, margin: 0, position: "absolute", inset: 0, display: "flex", flexDirection: "column" }
    : { padding: 0, marginBottom: bottomGap, display: "flex", flexDirection: "column", minHeight: 0, flexShrink: 1 };
  const list: CSSProperties = column
    ? { flex: 1, minHeight: 0, overflowY: "auto" }
    : { minHeight: 0, overflowY: "auto" };
  return (
    <div className="card" style={card}>
      {header}
      {between}
      <div data-testid={listTestId} ref={listRef} style={list}>
        {children}
      </div>
    </div>
  );
}
