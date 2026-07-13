import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Track, TrackDetailResponse } from "../lib/types";
import { usePlayer } from "../lib/player";
import { useWaveform } from "../hooks/useWaveform";
import { useTitle } from "../hooks/useTitle";
import { useIsMobile } from "../hooks/useIsMobile";

interface DupGroup { tracks: { file_path: string }[] }

function compareHref(paths: string[]): string {
  return `/compare?${paths.map((p) => `path=${encodeURIComponent(p)}`).join("&")}`;
}

function ext(path: string): string {
  const m = path.match(/\.([a-z0-9]+)$/i);
  return m ? m[1].toUpperCase() : "";
}
function baseName(p: string): string {
  const parts = p.split(/[\\/]/);
  return parts[parts.length - 1] || p;
}
function fmtDur(ms?: number | null): string {
  if (!ms) return "—";
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// Field rows shown in the comparison table. `get` returns the display string.
const ROWS: { key: string; label: string; get: (t: Track) => string }[] = [
  { key: "format", label: "Format", get: (t) => ext(t.file_path) || "—" },
  { key: "bpm", label: "BPM", get: (t) => (t.bpm != null ? t.bpm.toFixed(1) : "—") },
  { key: "confidence", label: "Confidence", get: (t) => (t.bpm_confidence != null ? t.bpm_confidence.toFixed(2) : "—") },
  { key: "detector", label: "Detector", get: (t) => t.detector || "—" },
  { key: "duration", label: "Duration", get: (t) => fmtDur(t.duration_ms) },
  { key: "title", label: "Title", get: (t) => t.title || "—" },
  { key: "artist", label: "Artist", get: (t) => t.artist || "—" },
  { key: "album", label: "Album", get: (t) => t.album || "—" },
  { key: "album_artist", label: "Album artist", get: (t) => t.album_artist || "—" },
  { key: "year", label: "Year", get: (t) => (t.year != null ? String(t.year) : "—") },
  { key: "isrc", label: "ISRC", get: (t) => t.isrc || "—" },
  { key: "status", label: "Status", get: (t) => t.status + (t.locked ? " · locked" : "") },
];

interface IsrcCandidate { source: string; isrc: string; title: string; artist: string; url: string }

function ColumnHeader({ track, onTrash, onKeep, showKeep, busy, suggested }: { track: Track; onTrash: (p: string) => void; onKeep: (p: string) => void; showKeep: boolean; busy: boolean; suggested: boolean }) {
  const { preview, toggle, isCurrent, playing, audioRef } = usePlayer();
  const qc = useQueryClient();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const active = isCurrent(track.file_path);
  useWaveform(canvasRef, audioRef, track.file_path, true, active);

  const [isrc, setIsrc] = useState(track.isrc || "");
  const [finding, setFinding] = useState(false);
  const [cands, setCands] = useState<IsrcCandidate[] | null>(null);
  const [spotifyUrl, setSpotifyUrl] = useState("");

  const save = useMutation({
    mutationFn: () => api.put("/api/track/tags", {
      file_path: track.file_path, title: track.title, artist: track.artist,
      album: track.album, album_artist: track.album_artist, track_no: track.track_no,
      disc_no: track.disc_no, year: track.year, isrc: isrc.trim(),
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["track", track.file_path] });
      qc.invalidateQueries({ queryKey: ["duplicates"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
    },
  });

  async function find() {
    setFinding(true);
    setCands(null);
    try {
      const r = await api.get<{ candidates: IsrcCandidate[]; spotify_search_url: string }>(
        `/api/isrc/lookup?artist=${encodeURIComponent(track.artist || "")}&title=${encodeURIComponent(track.title || "")}`);
      setCands(r.candidates);
      setSpotifyUrl(r.spotify_search_url);
    } finally {
      setFinding(false);
    }
  }

  const dirty = isrc.trim() !== (track.isrc || "");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <img
          src={`/api/track/cover?path=${encodeURIComponent(track.file_path)}`}
          alt=""
          onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = "hidden"; }}
          style={{ width: 48, height: 48, borderRadius: 6, objectFit: "cover", background: "var(--chip-bg)", flexShrink: 0 }}
        />
        <button
          className="btn btn-soft btn-sm"
          aria-label={active && playing ? "Pause" : "Play"}
          onClick={() => (active ? toggle() : preview({ path: track.file_path, title: track.title || baseName(track.file_path), artist: track.artist || undefined }))}
        >
          {active && playing ? "❚❚" : "▶"}
        </button>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontFamily: "var(--mono)", fontSize: 12, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={track.file_path}>
            {baseName(track.file_path)}
          </div>
          {suggested && <span className="chip chip--active" style={{ marginTop: 4, display: "inline-block" }}>suggested keep</span>}
        </div>
        {showKeep && (
          <button className="btn btn-soft btn-sm" disabled={busy} title="Keep this, trash the others" onClick={() => onKeep(track.file_path)}>
            Keep
          </button>
        )}
        <button
          className="btn btn-danger btn-sm"
          disabled={busy}
          title="Move to trash"
          onClick={() => {
            if (window.confirm(
              `Move "${baseName(track.file_path)}" to trash?\n\n` +
              `Recoverable until you purge (Settings → Trash). On Navidrome's next scan ` +
              `this file leaves the library — its favorites, playlist entries and play counts are lost.`))
              onTrash(track.file_path);
          }}
        >
          Trash
        </button>
      </div>
      <canvas ref={canvasRef} style={{ width: "100%", height: 56 }} />

      {/* ISRC edit + lookup */}
      <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ fontSize: 11, color: "var(--muted)" }}>ISRC</span>
        <input
          value={isrc}
          onChange={(e) => setIsrc(e.target.value.toUpperCase())}
          placeholder="—"
          style={{ fontFamily: "var(--mono)", fontSize: 11, width: 130, padding: "4px 6px" }}
        />
        <button className="btn btn-soft btn-sm" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? "…" : save.isSuccess && !dirty ? "Saved ✓" : "Save"}
        </button>
        <button className="btn btn-ghost btn-sm" disabled={finding} onClick={find}>
          {finding ? "Finding…" : "Find ISRC"}
        </button>
      </div>

      {cands != null && (
        <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "8px 10px", fontSize: 11, display: "flex", flexDirection: "column", gap: 6 }}>
          {cands.length === 0 ? (
            <span style={{ color: "var(--muted)" }}>No ISRC found via Spotify or MusicBrainz.</span>
          ) : (
            cands.map((c) => (
              <div key={c.source + c.isrc} style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <span className="chip chip--neutral">{c.source}</span>
                <button className="btn btn-bare btn-sm" style={{ fontFamily: "var(--mono)" }} onClick={() => { setIsrc(c.isrc); setCands(null); }} title="Use this ISRC">
                  {c.isrc}
                </button>
                <span style={{ color: "var(--muted)", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.artist} – {c.title}</span>
                {c.url && <a href={c.url} target="_blank" rel="noreferrer" style={{ color: "var(--accent-2)", marginLeft: "auto", flexShrink: 0 }}>open ↗</a>}
              </div>
            ))
          )}
          {spotifyUrl && (
            <a href={spotifyUrl} target="_blank" rel="noreferrer" style={{ color: "var(--accent-2)" }}>Open Spotify web player search ↗</a>
          )}
        </div>
      )}
    </div>
  );
}

export default function TrackCompare() {
  useTitle("Compare tracks");
  const [params, setParams] = useSearchParams();
  const paths = params.getAll("path");
  const qc = useQueryClient();
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const { endPreview } = usePlayer();
  useEffect(() => () => endPreview(), [endPreview]);  // leaving resumes the queue (dec 8)

  const trash = useMutation({
    mutationFn: (fp: string) => api.post("/api/track/trash", { file_path: fp }),
    onSuccess: (_data, fp) => {
      const remaining = paths.filter((p) => p !== fp);
      qc.invalidateQueries({ queryKey: ["duplicates"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      qc.invalidateQueries({ queryKey: ["tracks"] });
      // <2 left → group resolved, jump to the next; else keep comparing the rest.
      if (remaining.length < 2) gotoNextGroup();
      else setParams(new URLSearchParams(remaining.map((p) => ["path", p])), { replace: true });
    },
  });

  const [keeping, setKeeping] = useState(false);
  async function keepOnly(keepPath: string) {
    const others = paths.filter((p) => p !== keepPath);
    if (others.length === 0) return;
    if (!window.confirm(
      `Keep this track and move the other ${others.length} to trash?\n\n` +
      `Recoverable until you purge (Settings → Trash). On Navidrome's next scan those files ` +
      `leave the library — their favorites, playlist entries and play counts are lost. Locked tracks are skipped.`))
      return;
    setKeeping(true);
    try {
      for (const p of others) {
        try { await api.post("/api/track/trash", { file_path: p }); } catch { /* locked/error → skip */ }
      }
      qc.invalidateQueries({ queryKey: ["duplicates"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      qc.invalidateQueries({ queryKey: ["tracks"] });
      gotoNextGroup();  // group resolved → move on
    } finally {
      setKeeping(false);
    }
  }

  const queries = useQueries({
    queries: paths.map((p) => ({
      queryKey: ["track", p],
      queryFn: () => api.get<TrackDetailResponse>(`/api/track?path=${encodeURIComponent(p)}`),
    })),
  });

  const loading = queries.some((q) => q.isLoading);
  const tracks = queries.map((q) => q.data?.track).filter(Boolean) as Track[];

  // Duplicate-group stepping: find which group these paths belong to so we can
  // walk Prev/Next through every duplicate group without going back to Stats.
  const dupQ = useQuery({ queryKey: ["duplicates"], queryFn: () => api.get<{ groups: DupGroup[] }>("/api/duplicates") });
  const groups = dupQ.data?.groups ?? [];
  const key = (ps: string[]) => [...ps].sort().join("|");
  const currentKey = key(paths);
  const groupIdx = groups.findIndex((g) => key(g.tracks.map((t) => t.file_path)) === currentKey);
  const prevGroup = groupIdx > 0 ? groups[groupIdx - 1] : null;
  const nextGroup = groupIdx >= 0 && groupIdx < groups.length - 1 ? groups[groupIdx + 1] : null;

  // After resolving a group, step to the next one (or back to the list when done).
  const gotoNextGroup = () => navigate(nextGroup ? compareHref(nextGroup.tracks.map((t) => t.file_path)) : "/duplicates");

  const dismiss = useMutation({
    mutationFn: () => api.post("/api/duplicates/dismiss", { paths }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["duplicates"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      gotoNextGroup();
    },
  });

  // Suggest the best copy to keep: prefer lossless format, then a known BPM /
  // higher confidence. Only flag one when it's strictly better than the rest.
  const LOSSLESS = new Set(["flac", "wav", "wv", "aiff", "aif", "alac", "ape"]);
  const keepScore = (t: Track) => {
    const e = (t.file_path.match(/\.([a-z0-9]+)$/i)?.[1] || "").toLowerCase();
    return (LOSSLESS.has(e) ? 100 : 0) + (t.bpm != null ? 5 : 0) + (t.bpm_confidence ?? 0) * 4;
  };
  const scores = tracks.map(keepScore).sort((a, b) => b - a);
  const uniqueTop = scores.length > 1 && scores[0] > scores[1];
  const suggestedPath = uniqueTop ? tracks.reduce((a, b) => (keepScore(b) > keepScore(a) ? b : a)).file_path : "";

  // Rows whose values are not identical across all columns get highlighted.
  const differs = new Set<string>();
  for (const r of ROWS) {
    if (new Set(tracks.map(r.get)).size > 1) differs.add(r.key);
  }

  const cols = `160px repeat(${Math.max(tracks.length, 1)}, minmax(200px, 1fr))`;

  return (
    <>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 14, marginBottom: 20, flexWrap: "wrap" }}>
        <div>
          <h1 style={{ fontSize: 28, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 4 }}>Compare tracks</h1>
          <p style={{ fontSize: 13, color: "var(--muted)" }}>
            Side-by-side metadata, BPM and waveform. Differing fields are highlighted.
            {" "}<Link to="/duplicates" style={{ color: "var(--accent-2)" }}>← Back to duplicates</Link>
          </p>
        </div>
        {groupIdx >= 0 && (
          <>
            <div style={{ flex: 1 }} />
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
              {groups.length > 1 && (
                <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)" }}>
                  Group <span style={{ color: "var(--text)" }}>{groupIdx + 1} / {groups.length}</span>
                </span>
              )}
              <button className="btn btn-ghost btn-sm" disabled={dismiss.isPending} onClick={() => dismiss.mutate()} title="These aren't the same recording — stop flagging them">
                Not a duplicate
              </button>
              {groups.length > 1 && (
                <>
                  {prevGroup ? (
                    <Link to={compareHref(prevGroup.tracks.map((t) => t.file_path))} className="btn btn-ghost btn-sm">
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="19,4 7,12 19,20" /><rect x="5" y="4" width="2" height="16" /></svg>
                      Prev
                    </Link>
                  ) : <button className="btn btn-ghost btn-sm" disabled>Prev</button>}
                  {nextGroup ? (
                    <Link to={compareHref(nextGroup.tracks.map((t) => t.file_path))} className="btn btn-primary btn-sm">
                      Next
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="5,4 17,12 5,20" /><rect x="17" y="4" width="2" height="16" /></svg>
                    </Link>
                  ) : <button className="btn btn-primary btn-sm" disabled>Next</button>}
                </>
              )}
            </div>
          </>
        )}
      </div>

      {paths.length < 2 ? (
        <div className="card" style={{ color: "var(--muted)" }}>
          Select two or more tracks to compare (from the <Link to="/duplicates" style={{ color: "var(--accent-2)" }}>Duplicates</Link> page).
        </div>
      ) : loading ? (
        <p style={{ color: "var(--muted)" }}>Loading…</p>
      ) : isMobile ? (
        /* Mobile: the side-by-side grid can't fit a phone — stack one card per
           track instead, each with its own field list. Differing fields stay
           highlighted so the copies are still easy to tell apart. */
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {tracks.map((t) => (
            <div key={t.file_path} className="card">
              <ColumnHeader
                track={t}
                busy={trash.isPending || keeping}
                onTrash={(fp) => trash.mutate(fp)}
                onKeep={keepOnly}
                showKeep={tracks.length > 1}
                suggested={t.file_path === suggestedPath}
              />
              <div style={{ marginTop: 12 }}>
                {ROWS.map((r) => (
                  <div
                    key={r.key}
                    style={{
                      display: "grid", gridTemplateColumns: "104px minmax(0, 1fr)", gap: 10,
                      padding: "7px 0", borderBottom: "1px solid var(--border)",
                      background: differs.has(r.key) ? "var(--warn-bg)" : "transparent",
                    }}
                  >
                    <div style={{ fontSize: 12, color: "var(--muted)", fontWeight: 500 }}>{r.label}</div>
                    <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: differs.has(r.key) ? "var(--warn-fg)" : "var(--text)", wordBreak: "break-word" }}>
                      {r.get(t)}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <div className="card" style={{ minWidth: 520 }}>
            {/* Column headers: cover / play / waveform */}
            <div style={{ display: "grid", gridTemplateColumns: cols, gap: 12, alignItems: "end", paddingBottom: 14, borderBottom: "1px solid var(--border)" }}>
              <div />
              {tracks.map((t) => <ColumnHeader key={t.file_path} track={t} busy={trash.isPending || keeping} onTrash={(fp) => trash.mutate(fp)} onKeep={keepOnly} showKeep={tracks.length > 1} suggested={t.file_path === suggestedPath} />)}
            </div>
            {/* Field rows */}
            {ROWS.map((r) => (
              <div
                key={r.key}
                style={{
                  display: "grid", gridTemplateColumns: cols, gap: 12,
                  padding: "8px 0", borderBottom: "1px solid var(--border)",
                  background: differs.has(r.key) ? "var(--warn-bg)" : "transparent",
                }}
              >
                <div style={{ fontSize: 12, color: "var(--muted)", fontWeight: 500 }}>{r.label}</div>
                {tracks.map((t) => (
                  <div key={t.file_path} style={{ fontFamily: "var(--mono)", fontSize: 12, color: differs.has(r.key) ? "var(--warn-fg)" : "var(--text)", wordBreak: "break-word" }}>
                    {r.get(t)}
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
