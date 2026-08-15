import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import { useTitle } from "../hooks/useTitle";
import PageHeader from "../components/PageHeader";
import AddToPlaylistMenu from "../components/AddToPlaylistMenu";

/** Compare two playlists: what they share, and what each has that the other
 *  doesn't.
 *
 *  Local-first, like every playlist operation — nothing here writes back to
 *  Spotify or Navidrome. The only write available is "Save as playlist…", which
 *  copies a bucket's library-backed tracks into a Local playlist through the
 *  same AddToPlaylistMenu (and the same bulk endpoint) that "Add all to
 *  playlist…" uses, so the reported counts read identically.
 *
 *  Membership is compared server-side by the identity chain — matched library
 *  file, then ISRC, then normalized artist+title — so the same song counts as
 *  shared even when the two playlists hold different files of it, and a track
 *  you don't own at all still lines up by its tags. */

export interface DiffTrack {
  row_id: number;
  title: string;
  artist: string;
  album: string;
  /** The library file this row resolves to, or null when it's not on disk. */
  path: string | null;
  matched: boolean;
  bpm: number | null;
  isrc: string | null;
  duration_ms: number | null;
  cover_url: string | null;
  status: "have" | "missing" | "queued" | "removed";
}

export interface DiffPair {
  a: DiffTrack;
  b: DiffTrack;
  /** False when both playlists have the song but as two different files. */
  same_file: boolean;
}

export interface PlaylistDiffResponse {
  a: { id: number; name: string; source: string; count: number };
  b: { id: number; name: string; source: string; count: number };
  both: DiffPair[];
  only_a: DiffTrack[];
  only_b: DiffTrack[];
  counts: { both: number; only_a: number; only_b: number };
  /** Per bucket, the library-backed paths "Save as playlist…" would write. */
  paths: { both: string[]; only_a: string[]; only_b: string[] };
}

export type BucketKey = "both" | "only_a" | "only_b";

function fmtDuration(ms: number | null): string {
  if (!ms || ms <= 0) return "";
  const total = Math.round(ms / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

function TrackRow({ t, note }: { t: DiffTrack; note?: string }) {
  const body = (
    <>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {t.title || "—"}
        </div>
        <div style={{ fontSize: 11, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {[t.artist, t.album].filter(Boolean).join(" · ") || "—"}
          {note && <span style={{ color: "var(--accent-2)" }}> · {note}</span>}
        </div>
      </div>
      <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)", fontVariantNumeric: "tabular-nums" }}>
        {t.bpm ? `${Math.round(t.bpm)} BPM` : ""}
      </span>
      <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)", fontVariantNumeric: "tabular-nums" }}>
        {fmtDuration(t.duration_ms)}
      </span>
      {t.matched
        ? <span className="chip chip--have">✓ in library</span>
        : <span className="chip chip--missing">✗ not in library</span>}
    </>
  );
  const style = { gridTemplateColumns: "1fr auto auto auto", gap: 10, alignItems: "center" as const };
  // Matched rows link into the library; an unmatched one has nowhere to go.
  return t.path
    ? <Link className="pl-track-row" style={{ ...style, color: "inherit", textDecoration: "none" }}
            to={`/track?path=${encodeURIComponent(t.path)}`}>{body}</Link>
    : <div className="pl-track-row" style={style}>{body}</div>;
}

/** The three buckets with their tabs and per-bucket save action. Split out from
 *  the page (and exported) so it can be rendered against a fixed payload in the
 *  component test without a router or a query client. */
export function DiffBuckets({ data }: { data: PlaylistDiffResponse }) {
  const [bucket, setBucket] = useState<BucketKey>("both");
  const tabs: Array<{ key: BucketKey; label: string }> = [
    { key: "both", label: `In both · ${data.counts.both}` },
    { key: "only_a", label: `Only in ${data.a.name} · ${data.counts.only_a}` },
    { key: "only_b", label: `Only in ${data.b.name} · ${data.counts.only_b}` },
  ];
  const paths = data.paths[bucket];
  const rows: Array<{ key: number; track: DiffTrack; note?: string }> =
    bucket === "both"
      ? data.both.map((p) => ({
          key: p.a.row_id,
          track: p.a,
          // Worth saying: the playlists agree on the song but not on the file,
          // so "in both" isn't the same as "the same copy twice".
          note: p.same_file ? undefined : `different file in ${data.b.name}`,
        }))
      : data[bucket].map((t) => ({ key: t.row_id, track: t }));

  return (
    <div data-testid="diff-buckets">
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14, flexWrap: "wrap" }}>
        <div className="filter-pills" style={{ width: "fit-content" }}>
          {tabs.map((t) => (
            <button key={t.key} className={"filter-pill" + (bucket === t.key ? " active" : "")}
              onClick={() => setBucket(t.key)}>
              {t.label}
            </button>
          ))}
        </div>
        <div style={{ flex: 1 }} />
        {paths.length > 0 && (
          <AddToPlaylistMenu
            paths={paths}
            heading="Save these tracks to…"
            label={`Save as playlist… (${paths.length})`}
            title="Copy this bucket's library tracks into a local playlist"
            className="btn btn-soft btn-sm"
            iconSize={13}
          />
        )}
      </div>

      <div className="tracks-table">
        {rows.length === 0 ? (
          <div className="tracks-row-empty">Nothing in this bucket.</div>
        ) : (
          rows.map((r) => <TrackRow key={`${bucket}-${r.key}`} t={r.track} note={r.note} />)
        )}
      </div>

      {rows.length > 0 && paths.length < rows.length && (
        <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 10 }}>
          {rows.length - paths.length} of these {rows.length} aren't in your library, so
          they can't be saved into a playlist — a local playlist holds files, not
          source rows.
        </p>
      )}
    </div>
  );
}

export default function PlaylistDiff() {
  const [params] = useSearchParams();
  const a = params.get("a");
  const b = params.get("b");
  useTitle("Compare playlists");

  const diffQ = useQuery({
    queryKey: ["playlist-diff", a, b],
    queryFn: () => api.get<PlaylistDiffResponse>(`/api/playlists/diff?a=${a}&b=${b}`),
    enabled: !!a && !!b,
    retry: false,
  });

  const d = diffQ.data;
  const err = diffQ.error instanceof ApiError ? diffQ.error.message : diffQ.isError ? "Failed to compare" : null;

  return (
    <>
      <PageHeader
        title="Compare playlists"
        subtitle={d
          ? <>
              <Link to={`/playlist?id=${d.a.id}`} style={{ color: "var(--accent-2)" }}>{d.a.name}</Link>
              {" "}({d.a.count}) vs{" "}
              <Link to={`/playlist?id=${d.b.id}`} style={{ color: "var(--accent-2)" }}>{d.b.name}</Link>
              {" "}({d.b.count}) — matched by file, ISRC, then artist + title
            </>
          : "Two playlists, side by side."}
        actions={<Link className="btn btn-bare btn-sm" to="/playlists">Playlists</Link>}
      />

      {!a || !b ? (
        <p style={{ color: "var(--muted)" }}>Pick two playlists to compare from the Playlists page.</p>
      ) : err ? (
        <p style={{ color: "var(--err-fg)", fontSize: 13 }}>{err}</p>
      ) : !d ? (
        <p style={{ color: "var(--muted)" }}>Comparing…</p>
      ) : (
        <DiffBuckets data={d} />
      )}
    </>
  );
}
