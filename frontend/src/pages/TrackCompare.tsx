import { useRef } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQueries } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Track, TrackDetailResponse } from "../lib/types";
import { usePlayer } from "../lib/player";
import { useWaveform } from "../hooks/useWaveform";
import { useTitle } from "../hooks/useTitle";

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

function ColumnHeader({ track }: { track: Track }) {
  const { play, toggle, isCurrent, playing, audioRef } = usePlayer();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const active = isCurrent(track.file_path);
  useWaveform(canvasRef, audioRef, track.file_path, true, active);
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
          onClick={() => (active ? toggle() : play({ path: track.file_path, title: track.title || baseName(track.file_path), artist: track.artist || undefined }))}
        >
          {active && playing ? "❚❚" : "▶"}
        </button>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontFamily: "var(--mono)", fontSize: 12, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={track.file_path}>
            {baseName(track.file_path)}
          </div>
        </div>
      </div>
      <canvas ref={canvasRef} style={{ width: "100%", height: 56 }} />
    </div>
  );
}

export default function TrackCompare() {
  useTitle("Compare tracks");
  const [params] = useSearchParams();
  const paths = params.getAll("path");

  const queries = useQueries({
    queries: paths.map((p) => ({
      queryKey: ["track", p],
      queryFn: () => api.get<TrackDetailResponse>(`/api/track?path=${encodeURIComponent(p)}`),
    })),
  });

  const loading = queries.some((q) => q.isLoading);
  const tracks = queries.map((q) => q.data?.track).filter(Boolean) as Track[];

  // Rows whose values are not identical across all columns get highlighted.
  const differs = new Set<string>();
  for (const r of ROWS) {
    if (new Set(tracks.map(r.get)).size > 1) differs.add(r.key);
  }

  const cols = `160px repeat(${Math.max(tracks.length, 1)}, minmax(200px, 1fr))`;

  return (
    <>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 28, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 4 }}>Compare tracks</h1>
        <p style={{ fontSize: 13, color: "var(--muted)" }}>
          Side-by-side metadata, BPM and waveform. Differing fields are highlighted.
          {" "}<Link to="/stats" style={{ color: "var(--accent-2)" }}>← Back to duplicates</Link>
        </p>
      </div>

      {paths.length < 2 ? (
        <div className="card" style={{ color: "var(--muted)" }}>Select two or more tracks to compare (from Stats → Possible duplicates).</div>
      ) : loading ? (
        <p style={{ color: "var(--muted)" }}>Loading…</p>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <div className="card" style={{ minWidth: 520 }}>
            {/* Column headers: cover / play / waveform */}
            <div style={{ display: "grid", gridTemplateColumns: cols, gap: 12, alignItems: "end", paddingBottom: 14, borderBottom: "1px solid var(--border)" }}>
              <div />
              {tracks.map((t) => <ColumnHeader key={t.file_path} track={t} />)}
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
