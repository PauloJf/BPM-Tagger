import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { SettingsMap } from "../lib/types";
import { basename } from "../lib/paths";
import type { PlayerTrack } from "../lib/player";
import { useTitle } from "../hooks/useTitle";
import { useAuth } from "../lib/auth";
import PageHeader from "../components/PageHeader";
import AddToPlaylistMenu from "../components/AddToPlaylistMenu";
import { ArtistLinks } from "../components/ArtistLinks";
import { QueueActions } from "../components/QueueActions";
import { ArtToggle, Cover, useArtwork } from "../components/Artwork";

const PRESET_DEFAULTS = [
  { name: "Warmup", bpm: 120 }, { name: "Easy", bpm: 155 },
  { name: "Steady", bpm: 165 }, { name: "Tempo", bpm: 175 },
];

/** A track that can be run at the requested cadence, as /api/run/ready returns
 *  it — `run_bpm` is the BPM after octave folding and `rate` the playbackRate
 *  that lands it on the target. */
export interface ReadyTrack {
  path: string;
  title: string;
  artist: string;
  bpm: number;
  run_bpm: number;
  rate: number;
  starred: boolean;
  play_count: number | null;
  loudness_lufs: number | null;
}

interface ReadyResp {
  tracks: ReadyTrack[];
  target: number;
  count: number;
  octave_fold: boolean;
  stretch_limit_pct: number;
}

/** Presets from settings, accepting both the {name,bpm} shape and the legacy
 *  bare-number list — the same normalization the Run page does. */
export function normalizePresets(raw: unknown): Array<{ name: string; bpm: number }> {
  return [0, 1, 2, 3].map((i) => {
    const p = Array.isArray(raw) ? (raw as unknown[])[i] : undefined;
    if (p && typeof p === "object") {
      const o = p as { name?: unknown; bpm?: unknown };
      return { name: String(o.name ?? PRESET_DEFAULTS[i].name), bpm: Number(o.bpm ?? PRESET_DEFAULTS[i].bpm) };
    }
    if (typeof p === "number") return { name: PRESET_DEFAULTS[i].name, bpm: p };
    return PRESET_DEFAULTS[i];
  });
}

/** Ready rows → player queue entries. loudnessLufs rides along so a cadence
 *  queue gets the same volume levelling as every other queue. Exported for the
 *  component test. */
export function toPlayerTracks(tracks: ReadyTrack[]): PlayerTrack[] {
  return tracks.map((t) => ({
    path: t.path,
    title: t.title || basename(t.path),
    artist: t.artist || "",
    bpm: t.bpm ?? null,
    loudnessLufs: t.loudness_lufs ?? null,
  }));
}

/** "What can I run at X BPM?" — every library track that could be pulled onto a
 *  target cadence, by the exact rule the run queue uses (octave fold + max
 *  stretch). A virtual view, not a stored playlist: nothing here is persisted
 *  until you save it to one. */
export default function Cadence() {
  const [params, setParams] = useSearchParams();
  const { role } = useAuth();
  const [showArt, toggleArt] = useArtwork();

  const settingsQ = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<{ settings: SettingsMap }>("/api/settings"),
  });
  const presets = normalizePresets(settingsQ.data?.settings?.run_presets);

  const parsed = Number(params.get("bpm"));
  const target = parsed >= 30 && parsed <= 300 ? Math.round(parsed) : presets[1].bpm;
  useTitle(`Runnable at ${target} BPM`);

  const readyQ = useQuery({
    queryKey: ["run-ready", target],
    queryFn: () => api.get<ReadyResp>(`/api/run/ready?bpm=${target}`),
  });

  const tracks = readyQ.data?.tracks ?? [];
  const queue = toPlayerTracks(tracks);
  const canPlay = queue.length > 0;
  const noneReason = `Nothing in your library can be run at ${target} BPM within the current max-stretch limit`;

  return (
    <>
      <PageHeader
        title="Cadence"
        subtitle={
          readyQ.data
            ? <>
                <span style={{ fontFamily: "var(--mono)", color: "var(--text)" }}>{readyQ.data.count}</span>
                {" "}track{readyQ.data.count === 1 ? "" : "s"} runnable at{" "}
                <span style={{ fontFamily: "var(--mono)", color: "var(--text)" }}>{target}</span> BPM
                {" "}· within ±{readyQ.data.stretch_limit_pct.toFixed(1)}%
                {readyQ.data.octave_fold ? " · octave folding on" : ""}
              </>
            : "What your library can run, at any cadence."
        }
      />

      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
        <div className="filter-pills" style={{ width: "fit-content" }}>
          {presets.map((p) => (
            <button
              key={`${p.name}-${p.bpm}`}
              className={"filter-pill" + (p.bpm === target ? " active" : "")}
              onClick={() => setParams({ bpm: String(p.bpm) }, { replace: true })}
              title={`${p.name} — ${p.bpm} BPM`}
            >
              {p.name} <span style={{ fontFamily: "var(--mono)", opacity: 0.75 }}>{p.bpm}</span>
            </button>
          ))}
        </div>
        <div style={{ flex: 1 }} />
        <ArtToggle show={showArt} onToggle={toggleArt} />
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 18, flexWrap: "wrap" }}>
        <QueueActions tracks={queue} disabledTitle={noneReason} />
        {role === "admin" && canPlay && (
          <AddToPlaylistMenu
            paths={tracks.map((t) => t.path)}
            heading={`Save ${target} BPM tracks to…`}
            label="Add all to playlist…"
            title="Save this cadence view as a local playlist"
            className="btn btn-soft btn-sm"
            iconSize={13}
          />
        )}
        <div style={{ flex: 1 }} />
        {/* Playing from here is plain playback at native speed; running *to* the
            cadence — tempo lock, auto-refill — is the Run page's job. */}
        <Link className="btn btn-ghost btn-sm" to={`/run?bpm=${target}`}>Open in Run →</Link>
      </div>

      <div className="tracks-table">
        {tracks.length === 0 ? (
          <div className="tracks-row-empty">
            {readyQ.isLoading ? "Loading…"
              : readyQ.isError ? "Couldn't load the cadence view."
              : `Nothing runnable at ${target} BPM — try another preset, or raise the max stretch in Settings → Run.`}
          </div>
        ) : (
          tracks.map((t) => (
            <div key={t.path} className={"pl-track-row" + (showArt ? " pl-track-row--art" : "")}>
              <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--muted)" }}>
                {t.starred ? "★" : ""}
              </span>
              {showArt && <Cover path={t.path} size={38} />}
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  <Link to={`/track?path=${encodeURIComponent(t.path)}`} style={{ color: "inherit", textDecoration: "none" }} title="Open the track page">
                    {t.title || basename(t.path)}
                  </Link>
                </div>
                <div style={{ fontSize: 11, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  <ArtistLinks artist={t.artist} linkStyle={{ color: "inherit", textDecoration: "none" }} />
                </div>
              </div>
              {/* The run math, straight from the endpoint: what the track is,
                  what it folds to, and the rate that lands it on the target. */}
              <span
                style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)", whiteSpace: "nowrap", textAlign: "right" }}
                title={`${Math.round(t.bpm)} BPM native → ${Math.round(t.run_bpm)} after octave folding, played at ${t.rate.toFixed(3)}× to hit ${target}`}
              >
                {Math.round(t.bpm)} → {Math.round(t.run_bpm)} ×{t.rate.toFixed(2)}
              </span>
            </div>
          ))
        )}
      </div>
    </>
  );
}
