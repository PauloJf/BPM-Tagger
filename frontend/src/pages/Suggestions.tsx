import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { SuggestedArtist, SuggestedTrack, SuggestionsResponse } from "../lib/types";
import { useTitle } from "../hooks/useTitle";
import { useGrabberStatus } from "../hooks/useGrabberStatus";
import { useSuggestionQueue } from "../hooks/useSuggestionQueue";
import { PreviewButton } from "../components/trackBits";
import ArtistModal from "../components/ArtistModal";
import PageHeader from "../components/PageHeader";
import GrabberGate from "../components/GrabberGate";

/** "3 hours ago" style label from an ISO timestamp. */
function relativeTime(iso?: string | null): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (isNaN(then)) return "";
  const s = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} hour${h === 1 ? "" : "s"} ago`;
  const d = Math.floor(h / 24);
  return `${d} day${d === 1 ? "" : "s"} ago`;
}

function TrackRow({ t, onAdd, adding, onDismiss }: {
  t: SuggestedTrack;
  onAdd: () => void;
  adding: boolean;
  onDismiss: () => void;
}) {
  return (
    <div className="pl-track-row" style={{ gridTemplateColumns: "1fr auto auto", gap: 8 }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.title}</div>
        <div style={{ fontSize: 11, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {t.artist}{t.album ? ` · ${t.album}` : ""}
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        {(t.preview_url || (t.in_library && t.file_path)) && (
          <PreviewButton
            track={{ dz_track_id: t.dz_track_id, title: t.title, artist: t.artist, preview_url: t.preview_url }}
            libraryPath={t.in_library && t.file_path ? t.file_path : undefined}
          />
        )}
        {t.in_library ? (
          t.file_path ? (
            <Link className="chip chip--have sugg-action" to={`/track?path=${encodeURIComponent(t.file_path)}`}>✓ in library</Link>
          ) : (
            <span className="chip chip--have sugg-action">✓ in library</span>
          )
        ) : t.queued ? (
          <span className="chip chip--queued sugg-action">↓ queued</span>
        ) : (
          <button className="btn btn-soft btn-sm sugg-action" disabled={adding} onClick={onAdd}>Add to queue</button>
        )}
      </div>
      <button className="btn btn-bare btn-sm" title="Dismiss" aria-label="Dismiss" style={{ color: "var(--muted)", padding: "2px 6px" }} onClick={onDismiss}>✕</button>
    </div>
  );
}

function ArtistCard({ a, onOpen, onDismiss }: {
  a: SuggestedArtist;
  onOpen: () => void;
  onDismiss: () => void;
}) {
  const sub = a.have_tracks > 0
    ? `You have ${a.have_tracks} track${a.have_tracks === 1 ? "" : "s"}`
    : a.seeds.length
    ? `Because you like ${a.seeds[0]}`
    : "";
  return (
    <div className="card" style={{ padding: 10, display: "flex", flexDirection: "column", gap: 8, position: "relative" }}>
      <button
        className="btn btn-bare btn-sm"
        title="Dismiss" aria-label="Dismiss"
        style={{ position: "absolute", top: 4, right: 4, color: "var(--muted)", padding: "2px 6px", lineHeight: 1, zIndex: 1 }}
        onClick={onDismiss}
      >✕</button>
      <button
        onClick={onOpen}
        title={`Explore ${a.name}`}
        style={{ display: "flex", flexDirection: "column", gap: 8, background: "none", border: "none", padding: 0, cursor: "pointer", color: "inherit", textAlign: "left" }}
      >
        {a.image_url ? (
          <img src={a.image_url} alt="" loading="lazy" className="art-thumb" style={{ width: "100%", height: "auto", aspectRatio: "1 / 1" }} />
        ) : (
          <div className="art-thumb" style={{ width: "100%", aspectRatio: "1 / 1", display: "grid", placeItems: "center", fontSize: 32 }} aria-hidden>♪</div>
        )}
        <div style={{ minWidth: 0, width: "100%" }}>
          <div style={{ fontSize: 13, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={a.name}>{a.name}</div>
          {sub && <div style={{ fontSize: 11, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{sub}</div>}
        </div>
      </button>
      <button className="btn btn-sm btn-ghost" style={{ width: "100%" }} onClick={onOpen}>View artist</button>
    </div>
  );
}

export default function Suggestions() {
  useTitle("Suggestions");
  const qc = useQueryClient();
  const status = useGrabberStatus();
  const [modalArtist, setModalArtist] = useState<{ dzId: string; name: string } | null>(null);

  const q = useQuery({
    queryKey: ["suggestions"],
    queryFn: () => api.get<SuggestionsResponse>("/api/suggestions"),
    enabled: status.data?.enabled === true,
    refetchInterval: (query) =>
      (query.state.data as SuggestionsResponse | undefined)?.refreshing ? 3000 : false,
  });

  const refresh = useMutation({
    mutationFn: () => api.post("/api/suggestions/refresh"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["suggestions"] }),
  });

  const add = useSuggestionQueue();

  const dismiss = useMutation({
    mutationFn: (v: { kind: "artist" | "track"; key: string }) => api.post("/api/suggestions/dismiss", v),
    onMutate: async (v) => {
      await qc.cancelQueries({ queryKey: ["suggestions"] });
      const prev = qc.getQueryData<SuggestionsResponse>(["suggestions"]);
      qc.setQueryData<SuggestionsResponse>(["suggestions"], (old) => {
        if (!old) return old;
        if (v.kind === "artist") return { ...old, artists: (old.artists ?? []).filter((a) => a.dz_id !== v.key) };
        return { ...old, tracks: (old.tracks ?? []).filter((t) => t.dz_track_id !== v.key) };
      });
      return { prev };
    },
    onError: (_e, _v, ctx) => { if (ctx?.prev) qc.setQueryData(["suggestions"], ctx.prev); },
    onSettled: () => qc.invalidateQueries({ queryKey: ["suggestions"] }),
  });

  const data = q.data;
  const artists = data?.artists ?? [];
  const tracks = data?.tracks ?? [];
  const refreshing = !!data?.refreshing;
  const addingId = add.isPending ? add.variables?.dz_track_id : undefined;

  const addTrack = (t: SuggestedTrack) => add.mutate({
    dz_track_id: t.dz_track_id, title: t.title, artist: t.artist, album: t.album,
    duration_ms: t.duration_ms, cover_url: t.cover_url, suggestion_id: t.id,
  });

  return (
    <GrabberGate title="Suggestions" subtitle="Based on your top and starred artists">
      <PageHeader
        title="Suggestions"
        subtitle={<>
          Based on your top and starred artists
          {data?.computed_at && !refreshing ? ` · updated ${relativeTime(data.computed_at)}` : ""}
        </>}
        actions={
          <button
            className="btn btn-ghost btn-sm"
            disabled={refreshing || refresh.isPending}
            onClick={() => refresh.mutate()}
          >
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
        }
      />

      {data?.last_error ? (
        <div className="flash" style={{ background: "var(--warn-bg)", borderColor: "var(--warn-bd)", color: "var(--warn-fg)" }}>
          Last refresh had trouble reaching Deezer: {data.last_error}
        </div>
      ) : null}

      {q.isLoading ? (
        <div className="card" style={{ color: "var(--muted)" }}>Loading…</div>
      ) : artists.length === 0 && tracks.length === 0 ? (
        <div className="card" style={{ color: "var(--muted)" }}>
          {refreshing ? (
            "Building suggestions from your library…"
          ) : data?.seed_count === 0 ? (
            "No suggestions yet — they're derived from the artists in your library, so analyze some music first."
          ) : (
            <>
              No suggestions yet.{" "}
              <button className="btn btn-soft btn-sm" onClick={() => refresh.mutate()} disabled={refresh.isPending}>Refresh</button>
            </>
          )}
        </div>
      ) : (
        <>
          {artists.length > 0 && (
            <section style={{ marginBottom: 26 }}>
              <div className="section-label"><span>Suggested artists</span></div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))", gap: 12 }}>
                {artists.map((a) => (
                  <ArtistCard
                    key={a.dz_id}
                    a={a}
                    onOpen={() => setModalArtist({ dzId: a.dz_id, name: a.name })}
                    onDismiss={() => dismiss.mutate({ kind: "artist", key: a.name })}
                  />
                ))}
              </div>
            </section>
          )}

          {tracks.length > 0 && (
            <section>
              <div className="section-label"><span>Suggested tracks</span></div>
              <div className="tracks-table">
                {tracks.map((t) => (
                  <TrackRow
                    key={t.dz_track_id}
                    t={t}
                    adding={addingId === t.dz_track_id}
                    onAdd={() => addTrack(t)}
                    onDismiss={() => dismiss.mutate({ kind: "track", key: t.dz_track_id })}
                  />
                ))}
              </div>
            </section>
          )}
        </>
      )}

      {modalArtist && (
        <ArtistModal dzId={modalArtist.dzId} name={modalArtist.name} onClose={() => setModalArtist(null)} />
      )}
    </GrabberGate>
  );
}
