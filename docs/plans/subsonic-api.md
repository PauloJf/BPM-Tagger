# Plan: Optional Subsonic / OpenSubsonic API

> Current status of all plans is tracked in [STATUS.md](STATUS.md).

Status: **Phase 1 implemented** (Unreleased, 2026-09-23). Phases 2–3 open.

Phase 1 implementation notes (these amend the design below):

- **Subsonic password stored as-is, not encrypted.** Token auth needs the
  plaintext. Encryption at rest would add `cryptography`, a new dependency, and
  the key would sit next to the DB anyway. The password is generated (never the
  web password), API-only and revocable. That's the same posture as
  `navidrome_pass` in settings.json. API keys are stored sha256-hashed. Open
  question 2 is therefore moot.
- **Admin account only.** Player users are always playlist-scoped
  (`full_access` is no longer honoured), so Subsonic access for them needs
  scoped browse and search. That moves to Phase 2, with the per-account switch.
- **No albums table yet.** `getAlbumList2` aggregates `tracks` per request
  (`db/subsonic.py`), and album ids resolve through a 10 s in-memory id map
  (`web/subsonic/ids.py`). That's fine at the libraries tested. Add the table
  if a large library shows it's slow.
- **Restart-required toggle** (open question 1): the blueprint is registered
  at startup or not at all, and `/rest/` is in `_API_PREFIXES`, so a disabled
  install 404s instead of serving the SPA shell.
- Album and artist stars are accepted and ignored (only songs have a star
  column). `getPlaylists`, `getGenres` and `getNowPlaying` return empty lists,
  so clients that call them at startup don't error.
- Plaintext `p=` is allowed over https, from private or loopback addresses, or
  when `SUBSONIC_ALLOW_PLAIN_PASSWORD` is on.
- Verified against a live server with curl (a real FLAC library): browse,
  album detail with detected BPM, ranged stream, folder cover art, star,
  scrobble, XML envelope, and a bad-key error. No credentials appear in the
  server log. Not yet tried with a real client app.

## Goal

Let standard Subsonic clients (Symfonium, Feishin, DSub, Substreamer, play:Sub,
Sonos via bonob, …) browse and stream the library **directly from BPM Tagger**, so
that running without Navidrome costs nothing for people who listen through those
clients. As a bonus, detected BPM reaches those clients through OpenSubsonic's
`bpm` song field.

The API must be **opt-in, like every other non-core feature**, and must never
affect the core.

## Principle: the core stays the core

BPM Tagger's core is **BPM detection + tag writing** (+ the SQLite record of it).
Everything else — web UI, Run/Listen players, grabber, Navidrome integration,
ntfy, playlists, suggestions, lyrics — is an optional layer on top. The Subsonic
API is one more such layer:

- **Off by default** — `SUBSONIC_ENABLED=false`, with a Settings toggle.
- **Disabled means absent.** When off, the `/rest/*` blueprint is **not
  registered** at all (404 on every route), not "registered and refusing". No
  schema is touched beyond additive migrations, and no background work runs.
- **Requires the UI** (`ENABLE_UI=true`), because it rides the same
  Flask/Waitress server. With the UI off, the flag is ignored and a warning is
  logged at startup. A separate listener is not worth it.
- **Imports lazily** — `web/app.py` imports the blueprint module only inside the
  `if config["subsonic_enabled"]` branch, the same way `main.py` imports
  `GrabberService` and `PeriodicSync`.
- **No new mandatory dependency.** Phase 1 needs nothing beyond Flask and the
  stdlib (`xml.etree`, `hashlib`, `secrets`). Phase 3 transcoding reuses the
  `ffmpeg` binary already in the image.

### Audit: is the core really independent today? (2026-09-23)

Checked `bpm_tagger/main.py`, `scan/scanner.py`, `scan/watcher.py` and `bpm/*`.
**Yes, at runtime. There are a few packaging-level couplings and one unconditional
extra.**

Runtime gates (all good):

| Extra | Gate | Default |
|---|---|---|
| Web UI (and so every player, playlist, lyrics, suggestions route) | `ENABLE_UI` | off |
| Grabber (Spotify, downloads) | `GRABBER_ENABLED`, lazy import in `main.py` | off |
| Periodic playlist/star/play-count sync | `SYNC_INTERVAL_MINUTES > 0`, lazy import | off (0) |
| Navidrome rescan after a scan | `_trigger_navidrome_rescan` returns early unless URL, user and password are all set | unconfigured |
| ntfy notifications | `NotificationManager` is created only when `ntfy_url` and `ntfy_topic` are set | unconfigured |
| Install ping | opt-in consent | off |
| Loudness (for the player's volume leveling) | `MEASURE_LOUDNESS` | **on** |
| Tag index (artist/title/album columns for browse and matching) | `INDEX_TAGS` | **on** |

Couplings that don't break the core but are worth knowing about:

1. **Waveform peaks are computed for every track, unconditionally**
   (`scan/scanner.py`, `compute_waveform_peaks` after the tag write). They are
   only used by the UI's waveform view. That is extra CPU per track in a headless
   install, and there is no toggle. *Follow-up:* skip them when `ENABLE_UI` is
   off. The UI already recomputes them on demand when the DB value is NULL.
2. **Top-level imports pull integration code into the core.** `scan/scanner.py`
   and `scan/watcher.py` import `integrations.navidrome` at module top, which
   imports `requests` and `grabber.matching` (→ `rapidfuzz`). `index_tags()`
   also imports `grabber.matching` for its normalizers. None of this *runs*
   unconfigured, but the core can't be imported without `requests` and
   `rapidfuzz` installed. *Follow-up (optional):* move the normalizers to a
   neutral module (e.g. `bpm_tagger/text.py`) and make the rescan import lazy.
3. **One `requirements.txt` for everything.** Flask, Waitress, streamrip and
   yt-dlp are always installed, even for a core-only run. This is a packaging
   concern, not a runtime one. *Follow-up (optional):* a split into core and
   extras (`requirements-core.txt`, or `pip install bpm-tagger[ui,grabber]`
   extras).

All three are planned in [core-decoupling.md](core-decoupling.md). None of them
block this plan. The Subsonic work must not add a new one:
**nothing under `scan/`, `bpm/` or `main.py` imports from the Subsonic module.**

## Scope

### In scope

- A Flask blueprint at `/rest/<method>` and `/rest/<method>.view` (clients use
  both), accepting GET and POST form parameters.
- The Subsonic 1.16.1 response envelope in **XML (the default) and JSON**
  (`f=json`). JSONP (`f=jsonp`) is out of scope.
- OpenSubsonic extensions: `apiKeyAuthentication`, `songLyrics`, and the extended
  `Child` fields (`bpm`, `musicBrainzId`, `replayGain`, …).
- Read and browse, streaming, cover art, stars, scrobbles, playlists.

### Out of scope

- Podcasts, internet radio, shares, jukebox control, chat, bookmarks and play
  queue sync. Unsupported methods return error code 0 or 70, which clients
  handle.
- `getUsers`, `createUser` and friends. Account management stays in the web UI.
- Video.
- Replacing the web UI's own API. The SPA keeps using `/api/*`.

## Design

### Module layout

```
bpm_tagger/web/subsonic/
  __init__.py      # blueprint factory: build_subsonic_bp(config) -> Blueprint
  envelope.py      # ok()/error() → XML or JSON; one dict model, two serializers
  auth.py          # parse u/t/s, u/p, apiKey; resolve to an account
  ids.py           # stable opaque ids for song/album/artist/playlist
  browse.py        # getArtists, getArtist, getAlbum, getAlbumList2, getIndexes, getMusicDirectory
  search.py        # search3, getRandomSongs
  media.py         # stream, download, getCoverArt, getLyricsBySongId
  annotate.py      # star, unstar, getStarred2, scrobble
  playlists.py     # getPlaylists, getPlaylist, createPlaylist, updatePlaylist, deletePlaylist
  system.py        # ping, getLicense, getOpenSubsonicExtensions, getMusicFolders, getUser
```

Registered in `web/app.py` only when `subsonic_enabled`. All endpoints go into
`_CSRF_EXEMPT_ENDPOINTS`, since the API is stateless and every request carries
credentials. They also bypass `_enforce_player_scope`, which is session-based;
player scoping is re-applied inside the blueprint (see Roles below).

### Envelope

One internal representation per response: a dict tree. Two serializers:

- JSON: `{"subsonic-response": {"status": "ok", "version": "1.16.1", "type":
  "bpm-tagger", "serverVersion": "<VERSION>", "openSubsonic": true, ...}}`
- XML: the same tree, with scalar values as attributes and lists as repeated
  child elements, inside `<subsonic-response xmlns="http://subsonic.org/restapi">`.

Errors are HTTP 200 with `status="failed"` and `error{code,message}`. This is
spec behavior, and clients depend on it. The codes used are 10 (missing
parameter), 20/30 (version mismatch, which we never actually raise), 40 (wrong
credentials), 41 (token auth not supported for this user), 42/43/44
(OpenSubsonic auth errors), 50 (not authorized), and 70 (not found).

### Authentication — the one real decision

Subsonic's classic token auth is `t = md5(password + s)`, so **the server must
know the plaintext password**. BPM Tagger stores only werkzeug hashes (admin in
`settings.json`, players in `players.password_hash`), so classic token auth can't
be verified against those.

Decision: **separate, per-account Subsonic credentials, independent of the web
login password.**

1. **API key (preferred).** OpenSubsonic `apiKeyAuthentication` (`apiKey=...`,
   no `u`). Generated in the UI, shown once, and stored **hashed**
   (sha256 is enough for a 32-byte random key). Revocable. Supported by modern
   clients (Symfonium, Feishin, …).
2. **Subsonic password (legacy clients).** A separate "Subsonic password" per
   account, stored **encrypted at rest** (Fernet-style, keyed from the existing
   session secret in `settings.json`), so `md5(pw + salt)` can be verified. It is
   off per account until set. Generated by default (random, copyable) so people
   don't reuse their web password.
3. **`p=` plaintext / `p=enc:<hex>`.** Accepted only when `UI_PUBLIC_URL` is
   https or the request comes from a private-range address. Checked against the
   Subsonic password, never the web password.

Why not reuse the web password: it would force the web password out of hashed
storage, and it would put the one credential that unlocks the admin UI into
dozens of client configs. A separate secret keeps the blast radius at "can stream
music".

Where it lives:
- New table `subsonic_credentials(owner TEXT PRIMARY KEY, api_key_hash TEXT,
  password_enc TEXT, created_at TEXT, last_used_at TEXT)`. `owner` uses the
  existing `session_owner()` convention: `'admin'` or `'player:<id>'`. The shared
  guest (RUN_PASSWORD) gets no Subsonic access.
- Disabling or deleting a player invalidates its credentials, via the same
  `enabled` check that `login_required` does.

Brute force: reuse the per-IP lockout counters from `/api/login`
(`web/api/auth.py`), so failed `/rest` auth attempts count toward the same
lockout and show up under Settings → reset lockouts.

### Stable ids

| Entity | Id | Resolution |
|---|---|---|
| Song | `tr-<tracks.id>` | direct; `tracks.id` is an AUTOINCREMENT primary key and survives rescans because rows are keyed by `file_path` |
| Album | `al-<first 16 hex of sha1(norm(album_artist) + "\x1f" + norm(album))>` | lookup via an index table (below) |
| Artist | `ar-<first 16 hex of sha1(norm_name)>` | lookup via `track_artists.norm_name` |
| Playlist | `pl-<playlists.id>` | direct |
| Cover art | the album or song id itself | `getCoverArt?id=al-…` or `tr-…` |

Hash-based album and artist ids stay stable across rescans and DB rebuilds, as
long as the tags don't change. Retagging an album changes its id. That is
acceptable, since Navidrome behaves the same way.

**Albums index table.** `list_albums()` currently groups `tracks` on every call.
For `getAlbumList2` (sorts: random, newest, frequent, recent, starred,
alphabeticalByName, alphabeticalByArtist, byYear, byGenre) and paging at library
scale, add a derived table `albums(id TEXT PRIMARY KEY, album, album_artist,
year, genre, song_count, duration_ms, created_at, play_count, last_played,
starred)`. It is rebuilt incrementally from `index_tags()` and after play and star
changes. It's **only maintained when the Subsonic API is enabled**, so the core
scan pays nothing. On first enable, it is built in one pass on a background
thread, and `getAlbumList2` returns what's ready.

Open question: check whether `tracks` has a reliable "added" timestamp for
`newest`. If not, add `added_at` (additive migration, backfilled from
`analyzed_at`).

### Mapping to existing code

| Subsonic | Reuses |
|---|---|
| `getArtists` / `getIndexes` | `db.list_artists()` + `track_artists` |
| `getArtist` | `db.get_artist_tracks()` grouped into albums |
| `getAlbum` | `db.get_album_tracks()` |
| `getSong` | `db.get_track()` by id |
| `search3` | the Search page's query code in the db layer |
| `getRandomSongs` | the Listen shuffle draw (with `fromYear`, `toYear` and `genre` filters) |
| `stream` / `download` | the `/audio` logic (`_assert_in_music_dir` + `send_file(conditional=True)`), refactored into a shared helper so both routes do identical path validation |
| `getCoverArt` | the resolver behind the web UI's covers (`web/api/images.py`), with the `size` parameter mapped onto the existing thumbnail sizes |
| `star` / `unstar` / `getStarred2` | the `starred` column. **Star sync keeps working:** a Subsonic star is just a local star, and it propagates to Navidrome on the next sync if that's configured |
| `scrobble` (`submission=true`) | `db.bump_play_count()` + `_record_play_event()` from `web/api/media.py`, plus the existing optional Navidrome forward. `submission=false` (now playing) is accepted and ignored |
| `getLyricsBySongId` | `bpm/lyrics.py` (embedded and LRC, synced where available) |
| Playlists | Local playlists. Spotify and Navidrome mirrors are listed **read-only** (`updatePlaylist` on them → error 50) |

Song (`Child`) fields beyond the basics: `bpm` (rounded int), `starred`
(timestamp or absent), `playCount`, `played`, `replayGain` (from
`loudness_lufs` where the source is a tag), `path` (relative to `MUSIC_DIR`, never
absolute), `suffix`, `contentType`, `bitRate`, `duration`, `size`.

### Roles

- **Admin** credentials see everything.
- **Player users** see what the web app would let them see. `full_access=0`
  players see only their `player_playlists` and those playlists' tracks in browse
  and search. Streaming follows the existing documented rule in `/audio`:
  playlist association is organizational, not a security boundary, and path
  validation is the gate.
- `player_listen_mode='off'` doesn't restrict Subsonic. Listen mode is a web-app
  UX setting, and a Subsonic client is a listening client by definition. Instead,
  a **per-account "Subsonic access" switch** (off by default for players) is the
  real control.

## Phases

### Phase 1 — MVP (usable with Symfonium and Feishin)

- Settings: `SUBSONIC_ENABLED` env var + Settings toggle (restart required to
  register the blueprint, like other server-level toggles).
- Credentials UI: generate or revoke an API key and a Subsonic password per
  account (Settings for admin, the Players page for users).
- `ping`, `getLicense`, `getOpenSubsonicExtensions`, `getMusicFolders` (a single
  folder), `getUser`.
- `getArtists`, `getArtist`, `getAlbum`, `getSong`, `getAlbumList2`, `search3`,
  `getRandomSongs`.
- `stream` (raw file only; `maxBitRate` and `format` are ignored, and
  `estimateContentLength` is honored), `download`, `getCoverArt`.
- `star`, `unstar`, `getStarred2`, `scrobble`.
- Albums index table.
- XML + JSON envelopes.
- Tests: envelope golden files (XML and JSON), each auth mode plus lockout, id
  round-trips, role scoping, path traversal on `stream`, and blueprint absence
  when disabled.

Rough size: ~1,500 lines including tests.

### Phase 2 — parity

- Playlists: `getPlaylists`, `getPlaylist`, `createPlaylist`, `updatePlaylist`,
  `deletePlaylist`.
- Folder browsing for older clients: `getIndexes`, `getMusicDirectory` (path-based,
  directory ids = `dir-<sha1(relpath)>`).
- `getLyricsBySongId` (OpenSubsonic) + legacy `getLyrics`.
- `getGenres`, `getSongsByGenre`, `getAlbumList` (v1), `getArtistInfo2` and
  `getAlbumInfo2` (from the cached Deezer and MusicBrainz info where present).
- `getSimilarSongs2` / `getTopSongs`, backed by the Suggestions page's in-library
  matching, with BPM proximity as a tiebreaker.

### Phase 3 — polish

- **Transcoding:** `format` / `maxBitRate` → ffmpeg to opus or mp3, streamed.
  `timeOffset` for seeking. It's gated by a setting because it costs CPU.
- A BPM-aware extra: expose saved Run presets as virtual playlists
  (`pl-run-<preset>`), so a Subsonic client can play a cadence-built queue at
  native speed.
- A `getNowPlaying` stub, `getScanStatus` / `startScan` mapped to BPM Tagger's
  own scan (admin only).

## Security checklist

- [ ] Blueprint not registered when disabled (test asserts a 404).
- [ ] Every `stream`, `download` or cover path goes through
      `_assert_in_music_dir`. Ids never carry paths.
- [ ] `path` in responses is relative to `MUSIC_DIR`.
- [ ] Auth failures feed the shared per-IP lockout. Constant-time comparisons.
- [ ] API keys stored hashed. Subsonic passwords encrypted, never logged, never
      returned by any API after creation.
- [ ] Credentials in query strings are unavoidable in Subsonic. **Redact `t`, `s`,
      `p` and `apiKey` in any request logging** (check Waitress and our own logs).
- [ ] Plaintext `p=` only over https or from a private network.
- [ ] Disabled or deleted players lose access immediately.
- [ ] Per-account "Subsonic access" switch, off by default for players.
- [ ] CSP is unaffected (API responses aren't HTML).

## Config and docs

- `config.py`: `subsonic_enabled` (env `SUBSONIC_ENABLED`, default `false`),
  `subsonic_allow_plain_password` (default `false`), `subsonic_transcode`
  (Phase 3, default `false`).
- `.env.example`, `docker-compose.yml` comments.
- README and DOCKERHUB_README: an "Optional: Subsonic API" section, with client
  setup (server URL = the UI URL, username + API key or Subsonic password) and a
  tested-clients table.
- Positioning: this is what makes "Navidrome optional" true for people who use
  third-party clients. Update the README pillars and the CLAUDE.md "What It Does"
  wording in the same release.

## Open questions

1. **Restart vs live toggle.** Registering a blueprint after startup is awkward in
   Flask. Proposal: register it always, but gate every request on the live flag,
   which answers 404 when off. That breaks "disabled means absent" only
   cosmetically, while "restart required" matches how `ENABLE_UI` behaves. Leaning
   toward restart-required for purity. Decide before Phase 1.
2. **Where the encryption key lives.** Reuse the session secret (rotating it
   invalidates Subsonic passwords, so it needs a warning in the UI), or use a
   dedicated key in `settings.json`. Leaning toward dedicated.
3. **Multi-root libraries.** `getMusicFolders` returns one folder today. If
   `MUSIC_DIR` ever supports multiple roots, map them to folder ids.
4. **Client test matrix** for Phase 1 sign-off: Symfonium (Android), Feishin
   (desktop), play:Sub or Amperfy (iOS), DSub (legacy XML + token auth).
