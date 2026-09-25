# Subsonic API — endpoint reference

BPM Tagger can serve the [Subsonic](http://www.subsonic.org/pages/api.jsp) /
[OpenSubsonic](https://opensubsonic.netlify.app/) API, so Subsonic apps
(Symfonium, Feishin, DSub, play:Sub, Amperfy…) can browse and stream your library
directly. This page lists what's implemented and how each method behaves.
Design notes and history are in [`plans/subsonic-api.md`](plans/subsonic-api.md).

> `tests/test_subsonic_docs.py` checks that every method the server implements
> is listed here, so this page can't fall behind the code.

## Turning it on

| Variable | Default | |
|---|---|---|
| `SUBSONIC_ENABLED` | `false` | Serve `/rest`. Needs `ENABLE_UI=true`. Off means the routes don't exist (404). Also a toggle in **Settings → Subsonic API**, applied on restart. |
| `SUBSONIC_ALLOW_PLAIN_PASSWORD` | `false` | Accept `p=` (plain or `enc:` hex) from any address. Off: only over https (`UI_PUBLIC_URL` https) or from a private/loopback address. |
| `SUBSONIC_TRANSCODE` | `false` | Re-encode `stream` on request (ffmpeg → Opus/MP3). |
| `SUBSONIC_RUN_PLAYLISTS` | `true` | Expose each Run preset as a read-only playlist. |
| `SUBSONIC_ARTIST_INFO` | `true` | Biography and similar artists for `getArtistInfo2` (online lookups, cached 24 h). |
| `SUBSONIC_FETCH_LYRICS` | `false` | Look up lyrics a file doesn't have on LRCLIB when an app asks, and save them. |
| `UI_THREADS` | `0` (auto) | Server threads: 12, or 24 while the API is on. |

In the app, choose **OpenSubsonic** (or Subsonic) as the server type, **not
"Navidrome"**. An app's Navidrome mode uses Navidrome's own internal API, which
BPM Tagger doesn't provide.

## Requests and responses

- **URL:** `/rest/<method>` or `/rest/<method>.view`, with `GET` or `POST`
  (form body: OpenSubsonic `formPost`).
- **Common parameters:** `v` (API version, accepted but not enforced), `c` (the
  app's name, shown under Connected apps), `f` (`xml` default, `json`, `jsonp` +
  `callback`).
- **Envelope:** `version="1.16.1"`, `type="bpm-tagger"`, `serverVersion`,
  `openSubsonic=true`. Errors are HTTP 200 with `status="failed"` and
  `error{code,message}`, as the spec requires. Binary methods (`stream`,
  `download`, `getCoverArt`) return the bytes on success, or the error envelope
  on failure.
- **Stateless:** no session, no cookie, no CSRF. Every request authenticates.

### Authentication

Subsonic credentials are **separate from the web login**. Generate them per
account in **Settings → Subsonic API**; each is shown once and can be revoked.

| Mode | Parameters | Notes |
|---|---|---|
| API key (OpenSubsonic `apiKeyAuthentication`) | `apiKey` | Preferred. Stored sha256-hashed. Sending `u` as well is error 43. |
| Token | `u`, `t` = md5(password + `s`), `s` | Uses the account's generated Subsonic password. |
| Password | `u`, `p` (or `p=enc:<hex>`) | Only over https / from a private network unless `SUBSONIC_ALLOW_PLAIN_PASSWORD`; otherwise error 42. |

The admin's username is the admin username, or `admin` when the web UI uses a
password only. Player users log in with their own username.

Failed attempts count toward the same per-IP / per-account / global lockout as
the web login (Settings → Login protection).

### Accounts and scope

| | Admin | Player user |
|---|---|---|
| Sees | the whole library | only tracks of its assigned playlists (the Run-mode rule): every browse, search, list, and by-id lookup |
| Stream / star / scrobble | anything | only in-scope songs (error 70 otherwise) |
| Playlists | read all; create/edit/delete **Local** ones | read its own; no writes (error 50) |
| `startScan` | yes | no (error 50) |
| `getNowPlaying` | every account's apps | its own apps |

A player has no Subsonic access until the admin generates credentials for it.
Disabling or deleting the player cuts access on its next request.

### IDs

| Prefix | Meaning | Stable? |
|---|---|---|
| `tr-<n>` | song (`tracks.id`) | yes, across rescans |
| `al-<hash>` | album: normalized (album artist, album) | yes, while the tags don't change |
| `ar-<hash>` | artist: normalized credited name | yes, while the tags don't change |
| `dir-<hash>` | folder (path relative to the music folder) | yes, while the file isn't moved |
| `pl-<n>` | playlist | yes |
| `pl-run-<i>` | Run preset *i* as a virtual playlist | while the presets don't change |

Music folder id: `1` (a single folder).

### Error codes used

`0` generic / unsupported method · `10` missing parameter · `40` wrong
credentials (also while locked out) · `42` auth mechanism not allowed (plain
password over an insecure connection) · `43` conflicting auth parameters · `44`
invalid API key · `50` not authorized · `70` not found.

### OpenSubsonic extensions

`apiKeyAuthentication` v1 · `formPost` v1 · `songLyrics` v1 · `indexBasedQueue` v1. Song objects also
carry OpenSubsonic fields: **`bpm`** (the detected tempo), `genres[]`, and
`isrc[]`.

## Methods

### System

| Method | Notes |
|---|---|
| `ping` | |
| `getLicense` | Always `valid=true`. |
| `tokenInfo` | OpenSubsonic: the username an API key belongs to (apps call it after an API-key login). |
| `getOpenSubsonicExtensions` | The extensions above. |
| `getMusicFolders` | One folder, id `1`. |
| `getUser` | Your own user only; roles reflect the account (`adminRole`, `playlistRole` for the admin). |
| `getScanStatus` | `scanning` + `count` (tracks visible to you). |
| `startScan` | Admin. Starts BPM Tagger's own incremental scan (the web UI's Scan button). `fullScan=true` forces a re-analysis. |

### Browsing by tags (ID3)

| Method | Notes |
|---|---|
| `getArtists` | Indexed A–Z (`#` for the rest), ignoring the articles `The El La Los Las Le Les`. Every credited artist counts ("A, B" lists both). |
| `getArtist` | The artist + albums (sorted by year). |
| `getAlbum` | The album + songs (disc/track order). Case/accent variants of one album are merged. |
| `getSong` | |
| `getAlbumList2` | `type`: `random`, `newest`, `recent`, `frequent`, `starred`, `alphabeticalByName`, `alphabeticalByArtist`, `byYear` (`fromYear`/`toYear`; from > to sorts newest first), `byGenre` (`genre`). `size` ≤ 500, `offset`. Admin lists read the precomputed album index; its `starred` type and the album `starred` timestamp are still the **admin's** star (`tracks.starred`) — album lists are global and not per-account, unlike song stars below. |
| `getAlbumList` | Same as `getAlbumList2`, legacy element name. |
| `getGenres` | Name, `songCount`, `albumCount`. A tag like "House; Techno" counts under both. |
| `getSongsByGenre` | `genre` (case-insensitive), `count` ≤ 500, `offset`. |
| `getRandomSongs` | `size` ≤ 500, `fromYear`, `toYear`, `genre`. Analyzed tracks only, rating-weighted for the caller and skipping its own dislikes (see Ratings below). |
| `search3` | `query` across title/artist/album; separate artist/album/song `*Count` / `*Offset`. **An empty query (or `""`) pages through everything**, the full-library sync some apps use. |
| `search2` | Same as `search3`, legacy element name. |
| `getArtistInfo2` / `getArtistInfo` | `biography` (MusicBrainz → Wikidata → Wikipedia), `similarArtist`… (Deezer's related artists that are **in your library** and visible to you; `count` ≤ 100), and `small`/`medium`/`largeImageUrl` (the artist's Deezer photo, only when **Fetch artist images online** is on in Settings → Artwork, because apps load it straight from Deezer). Shares the web UI's 24 h cache. Waits up to 3 s for a lookup; a slower one finishes in the background for the next visit. Off with `SUBSONIC_ARTIST_INFO=false`. |
| `getAlbumInfo2` / `getAlbumInfo` | Empty info. |
| `getNowPlaying` | What connected apps are playing, from their now-playing scrobbles, or inferred from streams for apps that don't send them. |

### Browsing by folder

| Method | Notes |
|---|---|
| `getIndexes` | Top-level folders as the index, and any files at the root as `child`. Built from the library, never from files outside it. |
| `getMusicDirectory` | A `dir-` id lists subfolders + songs. Also accepts `al-` (the album's songs), `ar-` (the artist's albums) and `1` (the root). Songs' `parent` is their folder, so apps can navigate up. |

### Playlists

| Method | Notes |
|---|---|
| `getPlaylists` | Your visible playlists (`readonly` = not a Local playlist, or you're a player), then one read-only **"Run · <name> (<bpm> BPM)"** playlist per Run preset (tracks within ±4 % of the preset, half/double time included, rating-weighted for you and skipping your own dislikes — see Ratings below). Each `getPlaylist` on a Run preset draws fresh, so its songs and order can differ between calls; it isn't cached. |
| `getPlaylist` | Entries are the playlist's tracks you have in the library. |
| `createPlaylist` | Admin. `name` + `songId`… creates a Local playlist. With `playlistId`, replaces an existing Local playlist's songs. A song appears once per Local playlist. |
| `updatePlaylist` | Admin, Local only. `name`, `comment`, `songIdToAdd`…, `songIndexToRemove`… |
| `deletePlaylist` | Admin, Local only. Spotify/Navidrome mirrors and Run playlists give error 50. |

### Play queue (resume on another device)

| Method | Notes |
|---|---|
| `savePlayQueue` | `id`… (the queue, in order; repeats allowed), `current` (song id), `position` (ms). Replaces this account's saved queue; no `id` clears it. Songs outside a player's scope are dropped. |
| `getPlayQueue` | The saved queue: `entry`…, `current`, `position`, `changed`, `changedBy` (the app that saved it), `username`. No `playQueue` element when nothing is saved. |
| `savePlayQueueByIndex` | OpenSubsonic `indexBasedQueue`: like `savePlayQueue`, but `currentIndex` names the current *position*, which stays right when a song is queued twice. |
| `getPlayQueueByIndex` | The same saved queue with `currentIndex`. Both pairs read and write one queue per account. Songs deleted since are dropped, and the current position moves to the next surviving song. |

### Lyrics

| Method | Notes |
|---|---|
| `getLyricsBySongId` | OpenSubsonic `structuredLyrics`: synced lines carry `start` (ms), plain lyrics are line by line. Empty list when the track has none. |
| `getLyrics` | Legacy: `artist` + `title` → plain text (timestamps removed). |

Lyrics come from the file's embedded tag or a `.lrc` sidecar, the same place
the web player reads. With **`SUBSONIC_FETCH_LYRICS`** (or the toggle in
Settings → Subsonic API), a song without lyrics is looked up on LRCLIB when an
app asks, and saved per Settings → Lyrics (embedded or sidecar), so every app
and the web player have them from then on. The request waits up to 4 s. A
slower lookup finishes in the background and is served next time. At most 2
lookups run at once, and a song LRCLIB has nothing for (or marks instrumental)
isn't looked up again (retry it from the web UI). Without the setting, fill
lyrics from the web UI's per-track fetch or **Settings → Lyrics** bulk fill.

### Similar and top

| Method | Notes |
|---|---|
| `getSimilarSongs` | Seed: `tr-`, `al-` or `ar-` id. The seed's artists first, then tracks within ±5 % of its tempo (octave-folded). Offline, from the library only; rating-weighted for the caller, whose own dislikes are excluded (see Ratings below). `count` ≤ 500. |
| `getSimilarSongs2` | Same rule. |
| `getTopSongs` | `artist` (name): their tracks by play count. |

### Media

| Method | Notes |
|---|---|
| `stream` | The file, with HTTP range support. With `SUBSONIC_TRANSCODE`, `format` (`mp3`, `opus`, `raw`) and `maxBitRate` may re-encode it on the fly (no ranges; `timeOffset` seeks; `estimateContentLength=true` sets a length estimate). At most 4 transcodes run at once; beyond that the original is served. |
| `download` | Always the original file. |
| `getCoverArt` | `id`: song, album, artist, playlist or folder; optional `size`. For an artist, the artist's own photo when there is one locally (a custom pick in the web UI → `artist.jpg` beside the music → the downloaded cache). Otherwise, and for everything else: embedded art first, then `cover.jpg`/`folder.jpg`/`front.jpg` beside the files. Resized covers are cached under `/data/subsonic_covers`, with at most 2 first-time resizes at once (beyond that the original is sent). 404 when there's no art. |

### Annotation

| Method | Notes |
|---|---|
| `star` / `unstar` | `id` (songs), `albumId`, `artistId` (all repeatable). A song star/unstar sets **your own** derived star (rating ≥ 4 / 3 — see Ratings below); `albumId` / `artistId` stars are stored separately in `subsonic_stars` and stay library-wide. |
| `setRating` | OpenSubsonic. `id` (a song only — `tr-`) + `rating` 0–5, `0` clears it. Sets **your own** rating (`db/ratings.py`); an album or artist id, or an out-of-range/missing `rating`, is error `0` or `10`. |
| `getStarred2` / `getStarred` | Artists and albums starred library-wide, plus songs **you've** starred (your rating ≥ 4). |
| `scrobble` | `id`… with `time`… (ms). `submission=true` (default) counts a play (play count + per-account play event, forwarded to Navidrome when `NAVIDROME_SCROBBLE` is on). `submission=false` is "now playing": nothing recorded, but it feeds Connected apps and `getNowPlaying`. |

### Ratings and rating-weighted picking

Ratings are **per account** (`docs/plans/ratings-weighted-picking.md`): your `setRating` /
`star` / `unstar` never touch another account's view, and a song's `userRating` and
`starred` on every song object (`search3`, `getAlbum`, `getSong`, `getRandomSongs`,
playlists, `getStarred(2)`, `getSimilarSongs(2)`, `getTopSongs`, the play queue,
`getNowPlaying`…) are always **yours** — a star is a rating ≥ 4, and `userRating` is
omitted when you haven't rated the song. Dislike is a separate, per-account flag; it
excludes a song from `getRandomSongs`, `getSimilarSongs(2)` and the "Run · <preset>"
playlists for you, but never hides it from another account, and doesn't stop you playing
it directly.

`getRandomSongs`, `getSimilarSongs(2)` and the "Run · <preset>" playlists all draw from a
pool and then **sample it weighted by your rating** (unrated tracks get a neutral weight;
higher ratings are more likely, 1★ tracks less so; admin-configured in Settings → Ratings
& picking) — so repeated calls needn't return the same songs, and a Run playlist's
`getPlaylist` isn't cached between calls. The one exception is the **album/artist index**:
`getAlbumList2 type=starred` and every album/artist `starred` timestamp stay the **admin's**
library-wide star, since album and artist browsing isn't per-account.

## Not implemented

Any other method returns error `0` ("not supported by this server"). Notably:
podcasts, internet radio, shares, jukebox control, chat, bookmarks, user management (`getUsers`,
`createUser`…), video, rating albums or artists, and avatars. `getAlbumInfo2` returns no
content yet.
