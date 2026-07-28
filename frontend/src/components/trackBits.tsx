import { useState } from "react";
import type { Track } from "../lib/types";
import { basename } from "../lib/paths";
import { audioUrl } from "../lib/api";
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

/** A preview toggle for any track row. Runs through the normal ducking player:
 *  starting one while music plays fades the queue down and auto-resumes when
 *  the clip ends. The clip is ephemeral — it never persists into the saved
 *  queue.
 *
 *  Source order:
 *  - `libraryPath` — stream the user's own file through /audio (full track). Use
 *    when the row is backed by a library file, so preview isn't limited to the
 *    30-second Deezer clip when we already have the whole song.
 *  - `preview_url` — a pre-known 30 s clip URL (Deezer).
 *  - `resolveUrl()` — lazy path (the inbox resolves Deezer preview URLs on
 *    demand): the first click fetches the URL *and* starts playback (no
 *    click-twice). A resolved-but-empty result disables the button as "No
 *    preview available". */
export function PreviewButton({ track, resolveUrl, libraryPath }: {
  track: { dz_track_id: string; title: string; artist?: string; preview_url?: string };
  resolveUrl?: () => Promise<string>;   // lazy source, used when preview_url is absent
  libraryPath?: string;                 // when set, plays the user's own file (full track)
}) {
  const player = usePlayer();
  const [lazyUrl, setLazyUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // Full-library previews reuse the file's own path so pause/resume mirrors the
  // main queue's identity, while Deezer previews stay synthetic and ephemeral.
  const fullSrc = libraryPath ? audioUrl(libraryPath) : null;
  const syntheticPath = libraryPath ?? `preview:dz:${track.dz_track_id}`;
  const isThis = player.isCurrent(syntheticPath);
  const playing = isThis && player.playing;
  // A live preview that's ducking a queue: the pause gesture ends it and fades
  // back to the queue track, rather than pausing the clip in place and stranding
  // the ducked queue with no obvious way back to it.
  const ducking = playing && player.previewing;
  const effectiveUrl = fullSrc || track.preview_url || lazyUrl;   // "" once resolved-but-empty
  const unavailable = !fullSrc && lazyUrl === "";
  const pt: PlayerTrack = {
    path: syntheticPath, title: track.title, artist: track.artist,
    src: effectiveUrl || "", ephemeral: true,
  };
  const fullLabel = playing ? (ducking ? "Stop preview — resume the queue" : "Pause preview") : "Preview full track";
  const clipLabel = playing ? (ducking ? "Stop preview — resume the queue" : "Pause preview") : "Preview (30s clip)";
  const label = unavailable ? "No preview available" : fullSrc ? fullLabel : clipLabel;

  const onClick = () => {
    if (loading || unavailable) return;
    if (!effectiveUrl) {
      // Lazy path: resolve on first click, then play the resolved URL immediately.
      if (!resolveUrl) return;
      setLoading(true);
      resolveUrl()
        .then((url) => {
          setLazyUrl(url || "");
          if (url) player.preview({ ...pt, src: url });
        })
        .catch(() => setLazyUrl(""))
        .finally(() => setLoading(false));
      return;
    }
    if (!isThis) player.preview(pt);
    else if (ducking) player.endPreview();  // pause gesture → back to the queue
    else player.toggle();                   // standalone clip, or resume a paused preview
  };

  return (
    <button
      className="btn btn-bare btn-sm"
      disabled={loading || unavailable}
      style={{ padding: "2px 6px", color: playing ? "var(--accent-2)" : "var(--muted)", opacity: loading ? 0.5 : undefined }}
      title={label}
      aria-label={label}
      onClick={onClick}
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
