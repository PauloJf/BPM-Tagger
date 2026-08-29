import type { CSSProperties, MouseEvent, ReactNode } from "react";
import { Link } from "react-router-dom";
import { splitArtistCredits } from "../lib/artist";

/** Renders a (possibly multi-artist) credit string as one Link per artist —
 *  e.g. "Argy, SOLANCE" becomes two separate links to their own artist pages,
 *  joined the same way the tag reads. Renders nothing for an empty credit. */
export function ArtistLinks({
  artist, className, style, linkStyle, title, prefix, onLinkClick,
}: {
  artist?: string | null;
  className?: string;
  style?: CSSProperties;         // applied to the wrapping <span> (e.g. ellipsis)
  linkStyle?: CSSProperties;     // applied to each individual <Link>
  title?: string;                // defaults to the full, unsplit credit
  prefix?: ReactNode;            // e.g. "by "
  onLinkClick?: (e: MouseEvent) => void;
}) {
  const names = splitArtistCredits(artist);
  if (names.length === 0) return null;
  return (
    <span className={className} style={style} title={title ?? artist ?? undefined}>
      {prefix}
      {names.map((name, i) => (
        <span key={name + i}>
          {i > 0 && ", "}
          <Link to={`/artist?name=${encodeURIComponent(name)}`} style={linkStyle} onClick={onLinkClick}>
            {name}
          </Link>
        </span>
      ))}
    </span>
  );
}
