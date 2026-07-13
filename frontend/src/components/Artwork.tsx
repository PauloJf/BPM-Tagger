import { useEffect, useState } from "react";

const ART_KEY = "bpmtagger.showArtwork";

/** Show/hide artwork preference, persisted in localStorage (default: shown). */
export function useArtwork(): [boolean, () => void] {
  const [show, setShow] = useState(() => localStorage.getItem(ART_KEY) !== "0");
  const toggle = () =>
    setShow((s) => {
      localStorage.setItem(ART_KEY, s ? "0" : "1");
      return !s;
    });
  return [show, toggle];
}

export function ArtToggle({ show, onToggle }: { show: boolean; onToggle: () => void }) {
  return (
    <button
      className="btn btn-ghost btn-sm"
      style={{ padding: "6px 8px" }}
      onClick={onToggle}
      aria-pressed={show}
      title={show ? "Hide artwork" : "Show artwork"}
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="18" height="18" rx="2" />
        <circle cx="9" cy="9" r="2" />
        <path d="M21 15l-4.5-4.5L7 20" />
        {!show && <path d="M3 3l18 18" />}
      </svg>
    </button>
  );
}

/** Artist image (local artist.jpg → cached online fetch, server-resolved),
 *  falling back to the sample track's album cover, then the ♪ placeholder. */
export function ArtistImage({ name, fallbackPath, size, style }: { name: string; fallbackPath: string; size: number; style?: React.CSSProperties }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [name]);
  if (failed) return <Cover path={fallbackPath} size={size} round style={style} />;
  return (
    <img
      className="art-thumb art-thumb--round"
      src={`/api/artist/image?name=${encodeURIComponent(name)}`}
      alt=""
      loading="lazy"
      style={{ width: size, height: size, ...style }}
      onError={() => setFailed(true)}
    />
  );
}

/** Embedded cover art for a library file, with a ♪ placeholder when the file
 *  has none. The endpoint serves ETag/max-age headers so repeats hit cache. */
export function Cover({ path, size, round, style }: { path: string; size: number; round?: boolean; style?: React.CSSProperties }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [path]);
  const cls = "art-thumb" + (round ? " art-thumb--round" : "");
  const box = { width: size, height: size, ...style };
  if (failed) {
    return (
      <div className={cls} style={{ ...box, fontSize: Math.max(12, size * 0.34) }} aria-hidden>
        ♪
      </div>
    );
  }
  return (
    <img
      className={cls}
      src={`/api/track/cover?path=${encodeURIComponent(path)}`}
      alt=""
      loading="lazy"
      style={box}
      onError={() => setFailed(true)}
    />
  );
}
