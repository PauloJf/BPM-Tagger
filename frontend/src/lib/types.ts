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
}

export interface TrackDetailResponse {
  track: Track;
  back: string;
  prev_path: string | null;
  next_path: string | null;
  queue_pos: number | null;
  queue_total: number | null;
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

export interface Me {
  authenticated: boolean;
  version: string;
  csrf_token: string;
  review_count: number;
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

export interface Playlist {
  id: number;
  spotify_id: string;
  name: string;
  snapshot_id: string | null;
  enabled: number;
  image_url: string | null;
  track_count: number;
  last_synced_at: string | null;
  have_count: number;
  missing_count: number;
  queued_count: number;
  indexed_count: number;
}

export interface PlaylistTrack {
  id: number;
  spotify_track_id: string;
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
  derived_status: "have" | "missing" | "queued";
}
