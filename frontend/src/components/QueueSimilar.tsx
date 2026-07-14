import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { RelatedTrack } from "../lib/types";
import { usePlayer, type TempoLock } from "../lib/player";
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

/** "Queue similar" list for the now-playing artist (Part D): in-library tracks
 *  append to the play queue (cadence-filtered while a tempo lock is active);
 *  missing tracks go to the grab queue via the grabber-gated suggestion flow.
 *  Purely presentational panel — the host (PlayerBar popover, Run page card)
 *  provides the chrome and positioning. Source: Deezer artist radio, so
 *  "similar to this artist" rather than to the exact song. */
export default function QueueSimilar({ artist, onClose }: { artist: string; onClose?: () => void }) {
  const player = usePlayer();
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

  const inPlayQueue = new Set(player.queue.map((t) => t.path));
  const queueable = (t: RelatedTrack) =>
    !!t.file_path && !inPlayQueue.has(t.file_path) && cadenceEligible(t.bpm, lock);
  const queueableTracks = tracks.filter(queueable);

  const enqueue = (t: RelatedTrack) => {
    if (!t.file_path) return;
    player.enqueue({ path: t.file_path, title: t.title, artist: t.artist, bpm: t.bpm });
  };

  return (
    <>
      <div className="player-queue-head">
        <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          Similar to {name} · Deezer
        </span>
        <span style={{ display: "flex", gap: 4, flexShrink: 0 }}>
          {queueableTracks.length > 0 && (
            <button
              className="btn btn-bare btn-sm"
              onClick={() => queueableTracks.forEach(enqueue)}
              title={lock
                ? `Queue every in-library match that fits ${lock.target} BPM`
                : "Queue every in-library match"}
            >
              Queue all · {queueableTracks.length}
            </button>
          )}
          {onClose && (
            <button className="btn btn-bare btn-sm" onClick={onClose} aria-label="Close similar tracks">✕</button>
          )}
        </span>
      </div>
      <div className="player-queue-list">
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
                  {t.preview_url && (
                    <PreviewButton track={{ dz_track_id: t.dz_track_id, title: t.title, artist: t.artist, preview_url: t.preview_url }} />
                  )}
                  {t.in_library ? (
                    inQueue ? (
                      <span style={{ fontSize: 11, color: "var(--muted)", flexShrink: 0 }}>✓ in queue</span>
                    ) : offCadence ? (
                      <span
                        style={{ fontSize: 11, color: "var(--muted)", flexShrink: 0 }}
                        title={`Can't be stretched to ${lock!.target} BPM within ±${lock!.stretchLimitPct}%`}
                      >off cadence</span>
                    ) : (
                      <button className="btn btn-soft btn-sm" onClick={() => enqueue(t)}>Queue</button>
                    )
                  ) : t.queued ? (
                    <span className="chip chip--queued" style={{ flexShrink: 0 }}>↓ queued</span>
                  ) : grabberEnabled ? (
                    <button
                      className="btn btn-soft btn-sm"
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
