import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";

/** "Split…" — fan one playlist out into several Local playlists.
 *
 *  Local-first, like the rest of the playlist operations: the source is only
 *  read, and every output is a Local playlist named `<playlist> · <group>`.
 *  Re-running tops those up rather than duplicating them, so the dialog can be
 *  used as a refresh after the source grows.
 *
 *  Two modes:
 *   - cadence: one playlist per configured run preset, holding exactly what a
 *     run at that cadence would draw from here. The preview counts come from the
 *     server's own eligibility pass, which is the run queue's rule — so a track
 *     legitimately appears under more than one preset.
 *   - artist: one playlist per album artist with at least `min_group` tracks;
 *     everything below the threshold is reported rather than scattered into
 *     one-track playlists.
 *
 *  Admin-only, like all playlist management — the endpoints sit outside the
 *  default-deny player allowlist, so the caller must not render this for a
 *  player session. */

type SplitMode = "cadence" | "artist";

interface SplitPreview {
  source: { id: number; name: string };
  presets: Array<{ name: string; bpm: number }>;
  /** Preset name → how many of this playlist's tracks are runnable there. */
  cadence: Record<string, number>;
  artist: {
    groups: Array<{ group: string; count: number }>;
    skipped: Array<{ group: string; count: number; reason: string }>;
    min_group: number;
  };
}

interface SplitResult {
  mode: SplitMode;
  playlists: Array<{
    id: number; name: string; group: string; created: boolean;
    eligible: number; added: number; already_present: number; skipped_missing: number;
  }>;
  skipped: Array<{ group: string; count: number; reason: string }>;
}

const REASONS: Record<string, string> = {
  empty: "nothing runnable there",
  too_small: "too few tracks",
  no_artist: "no artist tag",
  limit: "over the split limit",
};

export default function PlaylistSplit({ playlistId, playlistName }: {
  playlistId: string;
  playlistName: string;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<SplitMode>("cadence");
  const [result, setResult] = useState<SplitResult | null>(null);
  const [err, setErr] = useState("");

  const previewQ = useQuery({
    queryKey: ["playlist-split-preview", playlistId],
    queryFn: () => api.get<SplitPreview>(`/api/playlists/${playlistId}/split`),
    enabled: open,
    staleTime: 30_000,
    retry: false,
  });

  const split = useMutation({
    mutationFn: () => api.post<SplitResult>(`/api/playlists/${playlistId}/split`, { mode }),
    onSuccess: (r) => {
      setResult(r);
      setErr("");
      qc.invalidateQueries({ queryKey: ["playlists"] });
      qc.invalidateQueries({ queryKey: ["run-playlists"] });
      qc.invalidateQueries({ queryKey: ["run-readiness"] });
    },
    onError: (e) => { setResult(null); setErr(e instanceof ApiError ? e.message : "Split failed"); },
  });

  function close() {
    setOpen(false);
    setResult(null);
    setErr("");
  }

  const p = previewQ.data;
  // What the chosen mode would produce, as (label, count) pairs — the preview and
  // the confirm button both read off this, so the button can never promise a
  // number the list above it doesn't show.
  const groups: Array<{ label: string; count: number }> = !p ? [] :
    mode === "cadence"
      ? p.presets.map((pr) => ({ label: `${pr.name} (${pr.bpm})`, count: p.cadence[pr.name] ?? 0 }))
                 .filter((g) => g.count > 0)
      : p.artist.groups.map((g) => ({ label: g.group, count: g.count }));
  const skipped = !p ? [] :
    mode === "cadence"
      ? p.presets.filter((pr) => (p.cadence[pr.name] ?? 0) === 0)
                 .map((pr) => ({ group: `${pr.name} (${pr.bpm})`, count: 0, reason: "empty" }))
      : p.artist.skipped;

  return (
    <>
      <button className="btn btn-soft btn-sm" onClick={() => setOpen(true)}
        title="Split this playlist into local playlists by cadence or by artist">
        Split…
      </button>

      {open && (
        <div role="dialog" aria-label="Split playlist" onClick={close}
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)", zIndex: 200, display: "grid", placeItems: "center", padding: 16 }}>
          <div className="card" onClick={(e) => e.stopPropagation()}
            style={{ width: "min(560px, 100%)", maxHeight: "84vh", overflowY: "auto" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
              <h2 style={{ fontSize: 15, fontWeight: 600, flex: 1 }}>Split “{playlistName}”</h2>
              <button className="btn btn-ghost btn-sm" onClick={close} aria-label="Close">✕</button>
            </div>

            <div className="filter-pills" style={{ width: "fit-content", marginBottom: 12 }}>
              {(["cadence", "artist"] as SplitMode[]).map((m) => (
                <button key={m} className={"filter-pill" + (mode === m ? " active" : "")}
                  onClick={() => { setMode(m); setResult(null); setErr(""); }}>
                  {m === "cadence" ? "By cadence" : "By artist"}
                </button>
              ))}
            </div>

            <p style={{ fontSize: 12, color: "var(--muted)", marginBottom: 12 }}>
              {mode === "cadence"
                ? "One local playlist per run preset, holding what a run at that cadence could actually use. A track that reaches two presets appears in both."
                : `One local playlist per album artist with at least ${p?.artist.min_group ?? 3} tracks here. Smaller artists are left alone.`}
              {" "}Existing playlists of the same name are topped up, not duplicated.
            </p>

            {result ? (
              <div style={{ fontSize: 13 }}>
                {result.playlists.length === 0 ? (
                  <p style={{ color: "var(--muted)" }}>Nothing to split — no group was big enough.</p>
                ) : (
                  result.playlists.map((out) => (
                    <div key={out.id} style={{ marginBottom: 4 }}>
                      <Link to={`/playlist?id=${out.id}`} style={{ color: "var(--accent-2)" }} onClick={close}>
                        {out.name}
                      </Link>
                      <span style={{ color: "var(--muted)", fontSize: 12 }}>
                        {" — "}{out.created ? "created" : "updated"}, added {out.added}
                        {out.already_present ? ` · ${out.already_present} already there` : ""}
                      </span>
                    </div>
                  ))
                )}
                {result.skipped.length > 0 && (
                  <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 8 }}>
                    Skipped: {result.skipped.map((s) =>
                      `${s.group || "untagged"} (${REASONS[s.reason] ?? s.reason})`).join(", ")}
                  </p>
                )}
              </div>
            ) : previewQ.isLoading ? (
              <p style={{ fontSize: 13, color: "var(--muted)" }}>Working out the groups…</p>
            ) : previewQ.isError ? (
              <p style={{ fontSize: 13, color: "var(--err-fg)" }}>Couldn't preview this split.</p>
            ) : (
              <>
                {groups.length === 0 ? (
                  <p style={{ fontSize: 13, color: "var(--muted)" }}>
                    Nothing here would form a group — this playlist has no matched tracks that qualify.
                  </p>
                ) : (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
                    {groups.map((g) => (
                      <span key={g.label} className="chip chip--neutral" style={{ textTransform: "none" }}>
                        {g.label} · {g.count}
                      </span>
                    ))}
                  </div>
                )}
                {skipped.length > 0 && (
                  <p style={{ fontSize: 12, color: "var(--muted)", marginBottom: 10 }}>
                    Skipped: {skipped.map((s) =>
                      `${s.group || "untagged"} (${REASONS[s.reason] ?? s.reason})`).join(", ")}
                  </p>
                )}
              </>
            )}

            {err && <div style={{ color: "var(--err-fg)", fontSize: 12, marginBottom: 8 }}>{err}</div>}

            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 12 }}>
              <button className="btn btn-ghost btn-sm" onClick={close}>
                {result ? "Done" : "Cancel"}
              </button>
              {!result && (
                <button className="btn btn-primary btn-sm"
                  disabled={split.isPending || groups.length === 0}
                  onClick={() => split.mutate()}>
                  {split.isPending ? "Splitting…"
                    : `Create ${groups.length} playlist${groups.length === 1 ? "" : "s"}`}
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
