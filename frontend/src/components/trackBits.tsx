import type { Track } from "../lib/types";
import { basename } from "../lib/paths";
import { usePlayer, type PlayerTrack } from "../lib/player";

/** Primary display name: the tag title when present, else the file name. */
export function trackTitle(t: Track): string {
  return (t.title && t.title.trim()) || basename(t.file_path);
}

/** Secondary line: "Artist · Album" using whichever of the two is available. */
export function trackSubtitle(t: Track): string {
  const artist = t.artist?.trim();
  const album = t.album?.trim();
  if (artist && album) return `${artist} · ${album}`;
  return artist || album || "";
}

export function confColor(v: number): string {
  return v >= 0.7 ? "var(--ok-fg)" : v >= 0.4 ? "var(--accent-2)" : "var(--warn-fg)";
}

export function ConfBar({ value }: { value: number | null }) {
  if (value == null) return <span style={{ color: "var(--muted)" }}>—</span>;
  return (
    <div className="conf-bar-wrap">
      <div className="conf-bar-track">
        <div className="conf-bar-fill" style={{ width: `${Math.round(value * 100)}%`, background: confColor(value) }} />
      </div>
      <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)", fontVariantNumeric: "tabular-nums" }}>
        {value.toFixed(2)}
      </span>
    </div>
  );
}

const Dot = () => <span className="badge-dot" />;

/** Status badge matching the Jinja precedence order exactly. */
export function StatusBadge({ track }: { track: Track }) {
  const t = track;
  if (t.status === "error")
    return (
      <span className="badge badge--error" title={t.error_message || "Analysis error"}>
        <Dot />
        error
      </span>
    );
  if (t.status === "deleted") return <span className="badge badge--error">deleted</span>;
  if (t.status === "pending") return <span className="badge badge--pending">pending</span>;
  if (t.needs_review && !t.reviewed)
    return (
      <span className="badge badge--review">
        <Dot />
        review
      </span>
    );
  if (t.reviewed)
    return (
      <span className="badge badge--reviewed">
        <Dot />
        reviewed
      </span>
    );
  if (t.locked)
    return (
      <span className="badge badge--locked">
        <Dot />
        locked
      </span>
    );
  return <span className="badge badge--ok">ok</span>;
}

export const FolderIcon = () => (
  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round">
    <path d="M3 7 a2 2 0 0 1 2 -2 h4 l2 2 h8 a2 2 0 0 1 2 2 v9 a2 2 0 0 1 -2 2 H5 a2 2 0 0 1 -2 -2 Z" />
  </svg>
);

export const ArrowIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
    <path d="M5 12 H19 M13 6 L19 12 L13 18" />
  </svg>
);

/** A 30-second Deezer preview toggle for any track row carrying a preview_url.
 *  Runs through the normal ducking player: starting one while music plays fades
 *  the queue down and auto-resumes when the clip ends. The clip is ephemeral —
 *  it never persists into the saved queue. */
export function PreviewButton({ track }: {
  track: { dz_track_id: string; title: string; artist?: string; preview_url: string };
}) {
  const player = usePlayer();
  const syntheticPath = `preview:dz:${track.dz_track_id}`;
  const isThis = player.isCurrent(syntheticPath);
  const playing = isThis && player.playing;
  // A live preview that's ducking a queue: the pause gesture ends it and fades
  // back to the queue track, rather than pausing the clip in place and stranding
  // the ducked queue with no obvious way back to it.
  const ducking = playing && player.previewing;
  const pt: PlayerTrack = {
    path: syntheticPath, title: track.title, artist: track.artist,
    src: track.preview_url, ephemeral: true,
  };
  return (
    <button
      className="btn btn-bare btn-sm"
      style={{ padding: "2px 6px", color: playing ? "var(--accent-2)" : "var(--muted)" }}
      title={playing ? (ducking ? "Stop preview — resume the queue" : "Pause preview") : "Preview (30s clip)"}
      aria-label={playing ? (ducking ? "Stop preview" : "Pause preview") : "Preview"}
      onClick={() => {
        if (!isThis) player.preview(pt);
        else if (ducking) player.endPreview();  // pause gesture → back to the queue
        else player.toggle();                   // standalone clip, or resume a paused preview
      }}
    >
      {playing ? (
        <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
          <rect x="6" y="4" width="4" height="16" rx="1" /><rect x="14" y="4" width="4" height="16" rx="1" />
        </svg>
      ) : (
        <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><polygon points="6,4 20,12 6,20" /></svg>
      )}
    </button>
  );
}
