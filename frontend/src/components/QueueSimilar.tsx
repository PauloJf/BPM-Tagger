import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { LibraryRelatedTrack, RelatedTrack } from "../lib/types";
import { usePlayer, type PlayerTrack, type TempoLock } from "../lib/player";
import { useGrabberStatus } from "../hooks/useGrabberStatus";
import { useSuggestionQueue } from "../hooks/useSuggestionQueue";
import { PreviewButton } from "./trackBits";

/** The octave candidate (×½ / ×1 / ×2) closest to the target — same fold the
 *  tempo lock applies at playback (lockRate in lib/player). */
function fold(bpm: number, target: number, octave: boolean): number {
  const cands = octave ? [bpm, bpm / 2, bpm * 2] : [bpm];
  return cands.reduce((a, b) => (Math.abs(target / b - 1) < Math.abs(target / a - 1) ? b : a));
}

/** Whether a track can sit on the active cadence: after octave folding, the
 *  stretch to reach the target must be within the clamp — otherwise lockRate
 *  would cap out and the track would play off-cadence, breaking the run. */
export function cadenceEligible(bpm: number | null | undefined, lock: TempoLock | null): boolean {
  if (!lock) return true;          // no lock → the ordinary play queue takes anything
  if (!bpm) return false;          // unknown BPM can't be folded onto a cadence
  const folded = fold(bpm, lock.target, lock.octave);
  return Math.abs(lock.target / folded - 1) <= lock.stretchLimitPct / 100 + 1e-9;
}

const SECTION_LABEL: React.CSSProperties = {
  padding: "8px 12px 4px", fontSize: 10, fontWeight: 600, letterSpacing: "0.06em",
  textTransform: "uppercase", color: "var(--muted)",
};
const EMPTY: React.CSSProperties = { padding: "6px 12px 10px", fontSize: 12, color: "var(--muted)" };

/** "Queue similar" for the now-playing track, in two sections:
 *
 *  - **From your library** (`path` given): offline picks from /api/related/library
 *    — the same artist first, then a similar tempo (the run's cadence while a
 *    tempo lock is active, so everything offered fits the run). Always playable.
 *  - **From Deezer** (Part D): the artist's Deezer radio. In-library matches
 *    append to the play queue (cadence-filtered under a lock); missing tracks go
 *    to the grab queue via the grabber-gated suggestion flow.
 *
 *  Purely presentational panel — the host (PlayerBar popover, Run page card)
 *  provides the chrome and positioning. */
export default function QueueSimilar({ artist, path, onClose }: { artist: string; path?: string; onClose?: () => void }) {
  const player = usePlayer();
  const { endPreview } = player;
  // Closing the panel (toggle off, run end, navigation) drops any preview clip
  // that's ducking the queue and fades back to the run track — mirrors the
  // TrackDetail/TrackCompare "leaving resumes the queue" behaviour, and makes
  // dismissing the panel a reliable escape hatch from a stray preview.
  useEffect(() => () => endPreview(), [endPreview]);
  const add = useSuggestionQueue();
  const grabber = useGrabberStatus();
  const grabberEnabled = grabber.data?.enabled === true;
  const name = (artist || "").trim();
  const lock = player.tempoLock;

  // Same key as RelatedPanel — an artist explored there is already cached here.
  const tracksQ = useQuery({
    queryKey: ["related-tracks", name],
    queryFn: () => api.get<{ tracks: RelatedTrack[] }>(`/api/related/tracks?name=${encodeURIComponent(name)}`),
    enabled: !!name,
    staleTime: Infinity,
  });
  const tracks = tracksQ.data?.tracks ?? [];

  // Library picks: keyed on the cadence too, so a new run target refetches.
  const libQ = useQuery({
    queryKey: ["related-library", path, lock?.target ?? null, lock?.stretchLimitPct ?? null],
    queryFn: () => {
      const qs = new URLSearchParams({ path: path!, count: "12" });
      if (lock) { qs.set("target", String(lock.target)); qs.set("stretch_pct", String(lock.stretchLimitPct)); }
      return api.get<{ tracks: LibraryRelatedTrack[] }>(`/api/related/library?${qs}`);
    },
    enabled: !!path,
    staleTime: 60_000,
  });
  const libTracks = libQ.data?.tracks ?? [];

  const inPlayQueue = new Set(player.queue.map((t) => t.path));
  const queueable = (t: RelatedTrack) =>
    !!t.file_path && !inPlayQueue.has(t.file_path) && cadenceEligible(t.bpm, lock);
  const libQueueable = libTracks.filter((t) => !inPlayQueue.has(t.path) && cadenceEligible(t.bpm, lock));

  const fromDeezer = (t: RelatedTrack): PlayerTrack =>
    ({ path: t.file_path!, title: t.title, artist: t.artist, bpm: t.bpm });
  const fromLibrary = (t: LibraryRelatedTrack): PlayerTrack =>
    ({ path: t.path, title: t.title, artist: t.artist, bpm: t.bpm, starred: t.starred,
       rating: t.rating ?? null, loudnessLufs: t.loudness_lufs });
  const enqueue = (t: RelatedTrack) => { if (t.file_path) player.enqueue(fromDeezer(t)); };

  // Queue all: library picks first, then Deezer's in-library matches, deduped.
  // One enqueueMany write — looping enqueue drops all but the last track.
  const queueAll: PlayerTrack[] = [];
  const seen = new Set<string>();
  for (const t of [...libQueueable.map(fromLibrary), ...tracks.filter(queueable).map(fromDeezer)]) {
    if (!seen.has(t.path)) { seen.add(t.path); queueAll.push(t); }
  }

  return (
    <>
      <div className="player-queue-head">
        <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          Similar to {name}
        </span>
        <span style={{ display: "flex", gap: 4, flexShrink: 0 }}>
          {queueAll.length > 0 && (
            <button
              className="btn btn-bare btn-sm"
              onClick={() => player.enqueueMany(queueAll)}
              title={lock
                ? `Queue every library match that fits ${lock.target} BPM`
                : "Queue every library match"}
            >
              Queue all · {queueAll.length}
            </button>
          )}
          {onClose && (
            <button className="btn btn-bare btn-sm" onClick={onClose} aria-label="Close similar tracks">✕</button>
          )}
        </span>
      </div>
      <div className="player-queue-list">
        {path && (
          <>
            <div className="similar-section-label" style={SECTION_LABEL}>From your library</div>
            {libQ.isLoading ? (
              <div style={EMPTY}>Loading…</div>
            ) : libTracks.length === 0 ? (
              <div style={EMPTY}>
                {lock ? `Nothing else in your library fits ${lock.target} BPM here.` : "Nothing similar in your library yet."}
              </div>
            ) : (
              libTracks.map((t) => {
                const inQueue = inPlayQueue.has(t.path);
                return (
                  <div key={t.path} className="player-queue-row" data-testid="similar-library-row">
                    <span style={{ flex: 1, minWidth: 0 }}>
                      <span style={{ display: "block", fontSize: 12, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.title}</span>
                      <span style={{ display: "block", fontSize: 11, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {t.artist}
                        {t.bpm != null && <span style={{ fontFamily: "var(--mono)" }}> · {Math.round(t.bpm)} BPM</span>}
                        <span title={t.reason === "artist" ? "Same artist" : "Similar tempo"}> · {t.reason === "artist" ? "same artist" : "similar tempo"}</span>
                      </span>
                    </span>
                    <span className="player-queue-actions" style={{ alignItems: "center", gap: 6 }}>
                      {inQueue ? (
                        <span className="sugg-action" style={{ fontSize: 11, color: "var(--muted)" }}>✓ in queue</span>
                      ) : (
                        <button className="btn btn-soft btn-sm sugg-action" onClick={() => player.enqueue(fromLibrary(t))}>Queue</button>
                      )}
                    </span>
                  </div>
                );
              })
            )}
            <div className="similar-section-label" style={SECTION_LABEL}>From Deezer</div>
          </>
        )}
        {tracksQ.isLoading ? (
          <div style={{ padding: 14, fontSize: 12, color: "var(--muted)", textAlign: "center" }}>Loading…</div>
        ) : tracks.length === 0 ? (
          <div style={{ padding: 14, fontSize: 12, color: "var(--muted)", textAlign: "center" }}>
            Nothing found on Deezer for this artist.
          </div>
        ) : (
          tracks.map((t) => {
            const inQueue = !!t.file_path && inPlayQueue.has(t.file_path);
            const offCadence = t.in_library && !!lock && !cadenceEligible(t.bpm, lock);
            return (
              <div key={t.dz_track_id} className="player-queue-row">
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ display: "block", fontSize: 12, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.title}</span>
                  <span style={{ display: "block", fontSize: 11, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {t.artist}
                    {t.in_library && t.bpm != null && <span style={{ fontFamily: "var(--mono)" }}> · {Math.round(t.bpm)} BPM</span>}
                  </span>
                </span>
                <span className="player-queue-actions" style={{ alignItems: "center", gap: 6 }}>
                  {(t.preview_url || (t.in_library && t.file_path)) && (
                    <PreviewButton
                      track={{ dz_track_id: t.dz_track_id, title: t.title, artist: t.artist, preview_url: t.preview_url }}
                      libraryPath={t.in_library && t.file_path ? t.file_path : undefined}
                    />
                  )}
                  {t.in_library ? (
                    inQueue ? (
                      <span className="sugg-action" style={{ fontSize: 11, color: "var(--muted)" }}>✓ in queue</span>
                    ) : offCadence ? (
                      <span
                        className="sugg-action"
                        style={{ fontSize: 11, color: "var(--muted)" }}
                        title={`Can't be stretched to ${lock!.target} BPM within ±${lock!.stretchLimitPct}%`}
                      >off cadence</span>
                    ) : (
                      <button className="btn btn-soft btn-sm sugg-action" onClick={() => enqueue(t)}>Queue</button>
                    )
                  ) : t.queued ? (
                    <span className="chip chip--queued sugg-action">↓ queued</span>
                  ) : grabberEnabled ? (
                    <button
                      className="btn btn-soft btn-sm sugg-action"
                      disabled={add.isPending && add.variables?.dz_track_id === t.dz_track_id}
                      onClick={() => add.mutate({
                        dz_track_id: t.dz_track_id, title: t.title, artist: t.artist,
                        album: t.album, duration_ms: t.duration_ms, cover_url: t.cover_url,
                      })}
                      title="Not in your library — add to the download queue"
                    >Grab</button>
                  ) : null}
                </span>
              </div>
            );
          })
        )}
      </div>
    </>
  );
}
