import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "./api";

/** Music Grabber link (optional): request a missing track from a separate Music
 *  Grabber instance. Everything here renders nothing until an admin connects it
 *  in Settings, and the request button only shows for accounts allowed to ask. */

export interface GrabberStatus { configured: boolean; public_url: string; can_request: boolean }

export interface GrabberRequestState {
  source_ref: string;
  request_id: number | null;
  status: string;            // pending, searching, downloading, awaiting_user, done, failed, in_library…
  needs_choice: boolean;
  path: string | null;
  error: string | null;
  active: boolean;
}

export function useGrabberStatus() {
  return useQuery({
    queryKey: ["grabber-status"],
    queryFn: () => api.get<GrabberStatus>("/api/grabber/status"),
    staleTime: 60_000,
    retry: false,
  });
}

/** States for these refs; polls every 10 s while any of them is still in flight. */
export function useGrabberRequests(refs: string[], enabled: boolean) {
  const key = refs.slice().sort().join(",");
  return useQuery({
    queryKey: ["grabber-requests", key],
    queryFn: () => api.get<{ requests: Record<string, GrabberRequestState> }>(
      `/api/grabber/requests?refs=${encodeURIComponent(key)}`),
    enabled: enabled && refs.length > 0,
    refetchInterval: (q) => {
      const reqs = q.state.data?.requests;
      return reqs && Object.values(reqs).some((r) => r.active) ? 10_000 : false;
    },
    retry: false,
  });
}

export type GrabberRequestBody = { playlist_track_ids: number[] } | { dz_id: string; title?: string; artist?: string };

export function useRequestFromGrabber(onDone?: (msg: string, ok: boolean) => void) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: GrabberRequestBody) =>
      api.post<{ requests: Record<string, GrabberRequestState>; errors: Record<string, { message: string }> }>(
        "/api/grabber/request", body),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["grabber-requests"] });
      const n = Object.keys(r.requests).length, bad = Object.keys(r.errors ?? {}).length;
      onDone?.(bad ? `Requested ${n}; ${bad} failed` : `Requested ${n} from Music Grabber`, !bad);
    },
    onError: (e) => onDone?.(e instanceof ApiError ? e.message : "Music Grabber didn't answer", false),
  });
}

const LABELS: Record<string, string> = {
  pending: "requested", searching: "searching", downloading: "downloading", transcoding: "downloading",
  tagging: "downloading", placing: "downloading", awaiting_user: "needs a choice", done: "downloaded",
  failed: "request failed", skipped: "skipped", cancelled: "cancelled", in_library: "already in Music Grabber's library",
};

/** A small chip for one request's state; "needs a choice" links to Music Grabber's Inbox. */
export function GrabberStateChip({ state, publicUrl }: { state: GrabberRequestState; publicUrl: string }) {
  const label = LABELS[state.status] ?? state.status;
  const cls = state.status === "failed" ? "chip chip--missing"
    : state.status === "done" || state.status === "in_library" ? "chip chip--have" : "chip chip--neutral";
  if (state.needs_choice && publicUrl) {
    return (
      <a className={cls} href={`${publicUrl}/inbox`} target="_blank" rel="noopener"
        title="Music Grabber found several candidates: pick one there">↗ {label}</a>
    );
  }
  return <span className={cls} title={state.error ?? undefined}>⇣ {label}</span>;
}

/** The per-row control: the state chip once requested (a failed one can be asked
 *  again), else a "Request" button. Renders nothing when requesting isn't allowed. */
export function GrabberRequestControl({ state, status, onRequest, busy, label = "this track" }: {
  state?: GrabberRequestState;
  status?: GrabberStatus;
  onRequest: () => void;
  busy?: boolean;
  label?: string;
}) {
  if (!status?.can_request && !state) return null;
  const retry = state && ["failed", "skipped", "cancelled"].includes(state.status);
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
      {state && <GrabberStateChip state={state} publicUrl={status?.public_url ?? ""} />}
      {status?.can_request && (!state || retry) && (
        <button className="btn btn-bare btn-sm" style={{ padding: "2px 6px", color: "var(--accent-2)", fontSize: 11 }}
          disabled={busy} onClick={onRequest} aria-label={`Request ${label} from Music Grabber`}
          title="Ask Music Grabber to fetch this track">
          {retry ? "Retry" : "Request"}
        </button>
      )}
    </span>
  );
}
