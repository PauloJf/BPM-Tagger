/**
 * The BPM Tagger 5-bar equalizer mark, used by the nav logo and the login logo.
 * Extracted so the glyph never drifts between the two copies. The bars are
 * drawn white for the gradient accent tile they sit on; pass `size` to scale.
 */
export default function BpmMark({ size = 17 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <rect x="3" y="10" width="2.4" height="4" rx="1" fill="white" opacity="0.7" />
      <rect x="7" y="6" width="2.4" height="12" rx="1" fill="white" opacity="0.9" />
      <rect x="11" y="3" width="2.4" height="18" rx="1" fill="white" />
      <rect x="15" y="6" width="2.4" height="12" rx="1" fill="white" opacity="0.9" />
      <rect x="19" y="10" width="2.4" height="4" rx="1" fill="white" opacity="0.7" />
    </svg>
  );
}
