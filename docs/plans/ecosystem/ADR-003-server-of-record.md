# ADR-003: Server of record (Navidrome) and Subsonic completeness

Status: **accepted as recommended** (2026-11-10), see [decisions.md](decisions.md).

## Context

CLAUDE.md today: "a Navidrome *companion*, not a Navidrome replacement (Navidrome stays the server
of record)". [subsonic-api.md](../subsonic-api.md) already built BPM Tagger's own `/rest` API with
the stated aim of making "Navidrome optional" true. In code, every Navidrome feature is already
inert without `NAVIDROME_URL` (`integrations/periodic_sync.py:43`), so "required" is a docs
position, not a code dependency.

## Options

| | (1) Navidrome stays required | (2) Navidrome optional; BPM Tagger is the ecosystem's server of record | (3) Replace Navidrome (remove the sync code) |
|---|---|---|---|
| Existing users | Nothing changes | Nothing changes for them; sync stays | Breaking: stars / playlists / play-count sync lost |
| New users | Three services minimum | Two (BPM Tagger + optional Music Grabber); Navidrome is a compose profile | Two |
| Per-user data | Split: Navidrome users vs BPM Tagger accounts, only the admin's stars and ratings sync | One owner of accounts, ratings and plays (ADR-002) | Same as (2) |
| Cost | Subsonic work is wasted | Subsonic gaps below; docs repositioning | (2) plus a breaking release for no gain |

## Recommendation

**(2).** Keep every Navidrome integration working and opt-in; stop describing Navidrome as
required. BPM Tagger's database stays the record for accounts, ratings, plays and playlists;
audio files and their tags stay the record for everything else. Music Grabber's playlist mirror
gets BPM Tagger as a target (its `/rest` playlist endpoints) so it no longer needs Navidrome.

## Subsonic / OpenSubsonic gaps (audit of `web/subsonic/handlers.py:1026-1060`)

Implemented: system, ID3 + folder browsing, search2/3, album lists, genres, playlists CRUD,
play queue (+ index-based), lyrics, similar/top songs, stream with transcoding, download, cover
art, star/unstar, setRating (songs), getStarred(2), scrobble, getNowPlaying, scan status.

| Gap | Clients that notice | Size |
|---|---|---|
| `replayGain` on songs (and ReplayGain / R128 tags to back it) | Symfonium, Feishin, Amperfy (volume levelling) | S, with roadmap slice 7 |
| Album / artist stars are global (`subsonic_stars` has no owner) | every multi-user setup | S, additive column + backfill to `admin` |
| `setRating` for albums / artists | Symfonium, Feishin | S |
| Bookmarks (`getBookmarks`, `createBookmark`, `deleteBookmark`) | DSub, Substreamer, Symfonium (audiobooks, long mixes) | S |
| `transcodeOffset` extension not advertised | Symfonium, Feishin (seek while transcoding) | S if `timeOffset` already works, else M |
| `getAvatar`, `getUsers` (self only), `getInternetRadioStations`, `getPodcasts`, `getShares` returning empty lists instead of an error | DSub, Substreamer (error toasts on start) | S |
| Jukebox, chat, video, real podcasts / radio / shares | none of the top clients need them | out of scope (unchanged from subsonic-api.md) |

A compatibility table per client (Symfonium, Feishin, Substreamer, DSub, Amperfy) is produced
from request traces replayed against the test app; real-device confirmation stays a human task.
