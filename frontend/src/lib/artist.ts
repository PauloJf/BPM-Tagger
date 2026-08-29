// Mirrors bpm_tagger.grabber.matching.split_artist_credits — conservative on
// purpose: only ','/';'/'/' reliably mark separate artist credits. '&'/'x'/
// 'and' routinely appear inside real act names ("Chase & Status", "Dimitri
// Vegas & Like Mike"), so splitting on them would break those apart.
const CREDIT_SPLIT = /\s*[,;/]\s*/;

/** Split a multi-artist credit string ("Argy, SOLANCE") into individual
 *  artist names, so each can link to its own artist page. */
export function splitArtistCredits(artist?: string | null): string[] {
  if (!artist) return [];
  return artist.split(CREDIT_SPLIT).map((s) => s.trim()).filter(Boolean);
}
