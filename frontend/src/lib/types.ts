// Shapes returned by the Flask JSON API (see bpm_tagger/web/api/).

export interface Track {
  id: number;
  file_path: string;
  file_hash: string | null;
  bpm: number | null;
  bpm_dr: number | null;
  bpm_es: number | null;
  bpm_lb: number | null;
  bpm_confidence: number | null;
  detector: string | null;
  analyzed_at: string | null;
  status: string;
  error_message: string | null;
  needs_review: number;
  reviewed: number;
  locked: number;
  waveform_peaks?: string | null;
  // Tag-index columns (grabber, M3+)
  title?: string | null;
  artist?: string | null;
  album?: string | null;
  album_artist?: string | null;
  track_no?: number | null;
  disc_no?: number | null;
  year?: number | null;
  isrc?: string | null;
  duration_ms?: number | null;
  managed?: number;
  spotify_track_id?: string | null;
  lyrics_status?: string | null;
  lyrics_synced?: number;
  starred?: number;
  disliked?: number;
  // Navidrome play data (null until pulled)
  play_count?: number | null;
  last_played?: string | null;
  // Integrated loudness (LUFS) + where it came from ('tag' | 'measured').
  // null = never measured, which the player treats as "play at full volume".
  loudness_lufs?: number | null;
  loudness_source?: string | null;
}

// ── Run mode ──────────────────────────────────────────────────────────────
export interface RunTrack {
  path: string;
  title: string;
  artist: string;
  bpm: number;
  starred: boolean;
  play_count?: number | null;
  run_bpm: number;   // BPM after octave fold (×½/×1/×2)
  rate: number;      // playbackRate that lands run_bpm on the target
  clamped?: boolean; // force-tempo only: rate was clamped, so playback isn't exactly on target
  from_playlist?: boolean;  // from the selected playlist itself, vs a library top-up
  loudness_lufs?: number | null;  // for the player's volume levelling
}

export interface RunQueueResponse {
  tracks: RunTrack[];
  target: number;
  count: number;
  octave_fold: boolean;
  tolerance_pct: number;
  prefer_starred: boolean;
  prefer_familiar?: boolean;
  recycled?: boolean;   // true when every non-excluded match ran out and the full pool was reshuffled
  topped_up?: boolean;  // true when a playlist run was filled out with library tracks (too few matched)
  playlist?: number | null;   // playlist id the pool was scoped to, or null for the whole library
  forced?: boolean;     // "play everything, force tempo" was on for this build
}

// A playlist offered as a run source, with how many tracks are actually runnable.
export interface RunPlaylistOption {
  id: number;
  name: string;
  source: PlaylistSource;
  image_url: string | null;
  available: number;
  total: number;
}

export interface LyricsResponse {
  lyrics: string;
  synced: boolean;
  source: "embedded" | "sidecar" | "none";
  status: string;
}

export interface ImageCandidate {
  source: string;
  name: string;
  detail: string;
  image_url: string;
  thumb_url: string;
}

export interface MetadataCandidate {
  source: string;
  title: string;
  artist: string;
  album: string;
  album_artist: string;
  track_no: number | null;
  disc_no: number | null;
  year: number | null;
  isrc: string;
  duration_ms: number | null;
  cover_url: string;
  url: string;
}

export interface TracksPage {
  tracks: Track[];
  total: number;
  page: number;
  pages: number;
  per_page: number;
  filter: string;
  all_count: number;
  review_count: number;
  locked_count: number;
  deleted_count: number;
  no_isrc_count: number;
  starred_count: number;
  disliked_count: number;
  unplaylisted_count: number;
}

export interface AudioQuality {
  format: string | null;        // container/codec label from the file extension
  bitrate: number | null;       // bits per second
  sample_rate: number | null;   // Hz
  bits_per_sample: number | null;
  channels: number | null;
  lossless: boolean | null;
}

export interface TrackDetailResponse {
  track: Track;
  back: string;
  prev_path: string | null;
  next_path: string | null;
  queue_pos: number | null;
  queue_total: number | null;
  quality?: AudioQuality | null;
  playback_buffer: number;
}

export interface ReviewPage {
  tracks: Track[];
  conf_threshold: number;
  total: number;
  page: number;
  pages: number;
  per_page: number;
}

export type Role = "admin" | "player";

export interface Me {
  authenticated: boolean;
  role: Role | null;
  version: string;
  csrf_token: string;
  review_count: number;
  install_ping_ask?: boolean;
  // Player identity (Phase 5). username is null for admin + the shared Guest login;
  // full_access is true only for admin and the Guest login. Named player users are
  // always playlist-scoped (full_access false → the Run source picker hides
  // library/starred for them).
  username?: string | null;
  full_access?: boolean;
  // Per-user accent hue (0–360), persisted server-side so it follows the account
  // across devices. null = no preference → the SPA keeps its localStorage value.
  accent_hue?: number | null;
  // Loudness levelling, server-configured and sent to every role's player.
  normalize_playback?: boolean;
  loudness_target_lufs?: number;
}

// Player-user account, as returned by the admin /api/players endpoints.
export interface PlayerUser {
  id: number;
  username: string;
  full_access: boolean;
  enabled: boolean;
  playlist_ids: number[];
  last_login_at: string | null;
}

export interface Waveform {
  peaks: number[];
  duration?: number;
  error?: string;
}

export interface Progress {
  is_scanning: boolean;
  is_paused: boolean;
  is_stopping: boolean;
  completed: number;
  total: number;
  cumulative_completed: number;
  current_file: string;
  current_step: string;
  step_index: number;
  step_total?: number;
  last_file: string;
  last_bpm: number | null;
}

export interface Stats {
  total: number;
  done: number;
  pending: number;
  error: number;
  needs_review: number;
  locked: number;
  [k: string]: unknown;
}

export type SettingsMap = Record<string, unknown>;

// ── Grabber (M3) ──────────────────────────────────────────────────────────
export interface SpotifyStatus {
  enabled?: boolean;
  configured: boolean;
  connected: boolean;
  scope?: string;
  redirect_uri?: string;
  last_error?: string;
}

export interface GrabberStatus {
  enabled: boolean;
  spotify?: SpotifyStatus;
  queue_counts?: Record<string, number>;
  active?: { id: number; status: string; progress: number; title: string; artist: string }[];
  inbox_count?: number;
  last_change?: string;
  versions?: { app?: string; yt_dlp?: string | null };
}

export interface GrabCandidate {
  id: number;
  provider: string;
  provider_track_id: string;
  title: string;
  artist: string;
  album: string;
  duration_ms: number | null;
  isrc: string | null;
  quality: string | null;
  score: number | null;
  score_breakdown: string | null;
  url: string | null;
  cover_url: string | null;
  rank: number;
}

export interface QueueItem {
  id: number;
  spotify_track_id: string | null;
  title: string | null;
  artist: string | null;
  album: string | null;
  album_artist?: string | null;
  duration_ms: number | null;
  isrc?: string | null;
  status: string;
  provider: string | null;
  error: string | null;
  attempts: number;
  progress: number;
  final_path: string | null;
  priority: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface InboxItem extends QueueItem {
  candidates: GrabCandidate[];
}

export type PlaylistSource = "spotify" | "navidrome" | "local";

export interface Playlist {
  id: number;
  source: PlaylistSource;
  spotify_id: string | null;
  navidrome_id: string | null;
  name: string;
  snapshot_id: string | null;
  enabled: number;
  image_url: string | null;
  track_count: number;
  last_synced_at: string | null;
  have_count: number;
  missing_count: number;
  queued_count: number;
  new_count: number;
  removed_count: number;
  indexed_count: number;
}

export interface NavidromePlaylist {
  navidrome_id: string;
  name: string;
  track_count: number;
  image_url: string;
  watched: boolean;
}

// ── Suggestions & Related (Deezer-derived) ─────────────────────────────────
export interface SuggestedArtist {
  id: number;
  dz_id: string;
  name: string;
  image_url: string;
  have_tracks: number;   // library tracks already owned (0-2; >=3 is filtered out)
  seeds: string[];
  score: number;
}

export interface SuggestedTrack {
  id: number;
  dz_track_id: string;
  title: string;
  artist: string;
  album: string;
  duration_ms: number | null;
  cover_url: string;
  preview_url: string;
  seeds: string[];
  in_library?: boolean;
  queued?: boolean;
  file_path?: string;
}

export interface SuggestionsResponse {
  enabled: boolean;
  artists?: SuggestedArtist[];
  tracks?: SuggestedTrack[];
  refreshing?: boolean;
  last_error?: string;
  computed_at?: string | null;
  seed_count?: number;
}

export interface RelatedArtist {
  dz_id: string;
  name: string;
  image_url: string;
  track_count: number;   // library tracks with this artist as primary (0 = not owned)
  library_name?: string; // library's display spelling, present when track_count > 0
}

export interface RelatedTrack {
  dz_track_id: string;
  title: string;
  artist: string;
  album: string;
  duration_ms: number | null;
  cover_url: string;
  preview_url: string;
  in_library?: boolean;
  queued?: boolean;
  file_path?: string;
  bpm?: number | null;   // set when in_library — lets "queue similar" feed the play queue
}

export interface DeezerArtistInfo {
  dz_id: string;
  name: string;
  image_url: string;
  nb_fan: number;
  nb_album: number;
}

export interface DeezerAlbumMeta {
  dz_album_id: string;
  title: string;
  cover_url: string;
  record_type: string;   // album | single | ep | compilation
  year: number | null;
  release_date: string;
  nb_tracks: number;
  explicit: boolean;
}

export interface DeezerArtistResponse {
  artist: DeezerArtistInfo;
  top_tracks: RelatedTrack[];
  albums: DeezerAlbumMeta[];
  singles: DeezerAlbumMeta[];
}

export interface DeezerAlbumDetail extends DeezerAlbumMeta {
  artist: string;
  tracks: RelatedTrack[];
}

export interface SpotifyPlaylist {
  spotify_id: string;
  name: string;
  image_url: string;
  track_count: number;
  owner: string;
  watched: boolean;
}

export interface PlaylistTrack {
  id: number;
  source_track_id: string | null;
  spotify_track_id: string | null;
  position: number;
  title: string;
  artist: string;
  album: string;
  album_artist: string;
  duration_ms: number | null;
  isrc: string | null;
  track_no: number | null;
  cover_url: string | null;
  match_status: string;
  matched_file_path: string | null;
  derived_status: "have" | "missing" | "queued" | "removed";
  is_new: number;
  first_seen_at: string | null;
  removed_at: string | null;
  // Enriched from the matched local library track ('have' rows only; null otherwise).
  local_bpm?: number | null;
  local_duration_ms?: number | null;
  local_detector?: string | null;
  local_artist?: string | null;
  local_album?: string | null;
  local_album_artist?: string | null;
}
