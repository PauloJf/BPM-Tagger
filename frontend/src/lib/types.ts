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
