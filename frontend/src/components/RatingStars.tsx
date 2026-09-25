import { useEffect, useRef, useState } from "react";

const LEVELS = [1, 2, 3, 4, 5];

function StarIcon({ filled, size }: { filled: boolean; size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={filled ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" aria-hidden>
      <polygon points="12,2.5 15,9 22,9.8 17,14.6 18.2,21.6 12,18.2 5.8,21.6 7,14.6 2,9.8 9,9" />
    </svg>
  );
}

interface RatingStarsBaseProps {
  /** Current rating, 1-5, or null for unrated. */
  value: number | null;
  /** Called with the new rating, or null when the current value is tapped again (clear). */
  onChange: (rating: number | null) => void;
  disabled?: boolean;
  /** Read-only: shows the value but no controls respond (guest view). */
  readOnly?: boolean;
  size?: number;
  /** Track title or similar, folded into the group's aria-label for context. */
  label?: string;
}

/** The full 5-star radiogroup widget: tap a star to set it, tap the current
 *  value again to clear it. Arrow keys / Home / End move focus and select
 *  (a radiogroup's roving tabindex — only one star is ever tab-stoppable). */
export function RatingStarsGroup({ value, onChange, disabled, readOnly, size = 16, label }: RatingStarsBaseProps) {
  const inert = disabled || readOnly;
  const [focusIdx, setFocusIdx] = useState(() => Math.max(0, (value ?? 1) - 1));
  useEffect(() => {
    if (value != null) setFocusIdx(value - 1);
  }, [value]);
  const refs = useRef<Array<HTMLButtonElement | null>>([]);

  function select(level: number) {
    if (inert) return;
    onChange(value === level ? null : level);
  }

  function onKeyDown(e: React.KeyboardEvent, idx: number) {
    if (inert) return;
    let next = idx;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = Math.min(LEVELS.length - 1, idx + 1);
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = Math.max(0, idx - 1);
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = LEVELS.length - 1;
    else if (e.key === " " || e.key === "Enter") { e.preventDefault(); select(LEVELS[idx]); return; }
    else return;
    e.preventDefault();
    setFocusIdx(next);
    refs.current[next]?.focus();
  }

  return (
    <div
      role="radiogroup"
      aria-label={label ? `Rating for ${label}` : "Rating"}
      aria-disabled={inert || undefined}
      style={{ display: "inline-flex", gap: 2 }}
    >
      {LEVELS.map((level, idx) => {
        const active = value != null && level <= value;
        const isCurrent = value === level;
        const tabbable = value != null ? level === value : idx === focusIdx;
        return (
          <button
            key={level}
            ref={(el) => { refs.current[idx] = el; }}
            type="button"
            role="radio"
            aria-checked={value === level}
            aria-label={isCurrent ? "Clear rating" : `Rate ${level} star${level === 1 ? "" : "s"}`}
            tabIndex={inert ? -1 : tabbable ? 0 : -1}
            disabled={disabled}
            className="btn btn-bare btn-sm"
            style={{ padding: 2, color: active ? "var(--warn-fg)" : "var(--muted)", cursor: inert ? "default" : "pointer" }}
            onClick={() => select(level)}
            onKeyDown={(e) => onKeyDown(e, idx)}
            onFocus={() => setFocusIdx(idx)}
          >
            <StarIcon filled={active} size={size} />
          </button>
        );
      })}
    </div>
  );
}

/** Shared rating control. `compact` (a single star + the number, for dense
 *  rows and the mobile player bar) opens a popover with the full widget;
 *  otherwise the 5-star row renders inline. `readOnly` hides all controls
 *  (guest view) and just shows the value, if any. */
export function RatingStars({
  value, onChange, disabled, readOnly, compact, size = 16, label,
}: RatingStarsBaseProps & { compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!compact) {
    return <RatingStarsGroup value={value} onChange={onChange} disabled={disabled} readOnly={readOnly} size={size} label={label} />;
  }

  const compactLabel = value != null ? `Rating: ${value} star${value === 1 ? "" : "s"}` : "Not rated";

  return (
    <div ref={wrapRef} style={{ position: "relative", display: "inline-flex" }}>
      <button
        type="button"
        className="btn btn-bare btn-sm"
        disabled={disabled || readOnly}
        aria-haspopup={readOnly ? undefined : "true"}
        aria-expanded={readOnly ? undefined : open}
        aria-label={readOnly ? compactLabel : `${compactLabel} — click to change`}
        title={readOnly ? compactLabel : `${compactLabel} — click to change`}
        style={{ display: "inline-flex", alignItems: "center", gap: 3, padding: "2px 5px", color: value ? "var(--warn-fg)" : "var(--muted)", cursor: readOnly ? "default" : "pointer" }}
        onClick={() => { if (!readOnly && !disabled) setOpen((o) => !o); }}
      >
        <StarIcon filled={!!value} size={size} />
        <span style={{ fontFamily: "var(--mono)", fontSize: 11, fontVariantNumeric: "tabular-nums" }}>{value ?? "–"}</span>
      </button>
      {open && !readOnly && (
        <div
          className="card"
          style={{
            position: "absolute", zIndex: 30, top: "calc(100% + 6px)", right: 0,
            padding: "8px 10px", display: "flex", alignItems: "center", gap: 4,
            boxShadow: "0 8px 24px -8px rgba(0,0,0,0.4)", whiteSpace: "nowrap",
          }}
        >
          <RatingStarsGroup
            value={value}
            onChange={(v) => { onChange(v); setOpen(false); }}
            disabled={disabled}
            size={size + 2}
            label={label}
          />
        </div>
      )}
    </div>
  );
}

/** Separate dislike toggle, kept alongside RatingStars everywhere a star used
 *  to live. A hard exclusion independent of rating (a 1★ track can still be
 *  liked; a disliked track is never auto-picked whatever its rating). */
export function DislikeButton({
  on, onToggle, disabled, readOnly, size = 15,
}: { on: boolean; onToggle: () => void; disabled?: boolean; readOnly?: boolean; size?: number }) {
  if (readOnly) return null;
  return (
    <button
      type="button"
      className="btn btn-bare btn-sm"
      disabled={disabled}
      style={{ padding: 4, color: on ? "var(--err-fg)" : "var(--muted)", flexShrink: 0 }}
      onClick={onToggle}
      aria-pressed={on}
      aria-label={on ? "Remove dislike" : "Dislike"}
      title={on ? "Remove dislike — eligible again" : "Dislike — never picked again"}
    >
      <svg width={size} height={size} viewBox="0 0 24 24" fill={on ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17" />
      </svg>
    </button>
  );
}
