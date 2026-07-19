import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { SettingsMap } from "../lib/types";
import { Toggle } from "../components/Toggle";
import PlayerUsers from "../components/PlayerUsers";
import { useTitle } from "../hooks/useTitle";
import { useGrabberStatus } from "../hooks/useGrabberStatus";
import PageHeader from "../components/PageHeader";

type Saved = "" | "saving" | "ok" | "err";

const SIDEBAR = [
  ["sec-grabber", "Grabber"],
  ["sec-password", "Password"],
  ["sec-run-access", "Player Access"],
  ["sec-ntfy", "Notifications"],
  ["sec-scan", "Scan Behavior"],
  ["sec-mode", "Operating Mode"],
  ["sec-navidrome", "Navidrome"],
  ["sec-playback", "Playback"],
  ["sec-run", "Run Mode"],
  ["sec-artwork", "Artwork"],
  ["sec-lyrics", "Lyrics"],
  ["sec-isrc", "ISRC"],
  ["sec-trash", "Trash"],
  ["sec-deleted", "Deleted Tracks"],
  ["sec-version", "Version"],
  ["sec-restart", "Restart"],
];

interface IsrcCand { source: string; isrc: string; title: string; artist: string; url: string }
interface FillStatus {
  running: boolean; total: number; done: number; filled: number;
  unresolved: { file_path: string; title: string; artist: string; candidates: IsrcCand[] }[];
}
interface LyricsFillStatus {
  running: boolean; total: number; done: number; filled: number; not_found: number;
}

function fmtBytes(n: number): string {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB"];
  let v = n, i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(i ? 1 : 0)} ${u[i]}`;
}

const MODES = ["watch", "watch_all", "scan_unscanned", "scan_all", "scan_review", "report"];

function SaveButton({ state, label, ghost }: { state: Saved; label: string; ghost?: boolean }) {
  return (
    <button type="submit" className={"btn btn-sm " + (ghost ? "btn-ghost" : "btn-primary")} disabled={state === "saving"}>
      {state === "saving" ? "Saving…" : state === "ok" ? "Saved ✓" : label}
    </button>
  );
}

export default function Settings() {
  useTitle("Settings");
  const qc = useQueryClient();
  const { version } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const grabberStatus = useGrabberStatus();
  const settingsQ = useQuery({ queryKey: ["settings"], queryFn: () => api.get<{ settings: SettingsMap; env_locked?: string[] }>("/api/settings") });
  const trashQ = useQuery({ queryKey: ["trash"], queryFn: () => api.get<{ count: number; bytes: number }>("/api/trash") });
  const [purging, setPurging] = useState(false);
  const deletedQ = useQuery({ queryKey: ["deleted"], queryFn: () => api.get<{ count: number }>("/api/deleted") });
  const [purgingDeleted, setPurgingDeleted] = useState(false);

  // Bulk ISRC fill: poll status while the job runs; hide rows the user resolves.
  const fillQ = useQuery({
    queryKey: ["isrc-fill"],
    queryFn: () => api.get<FillStatus>("/api/isrc/fill/status"),
    refetchInterval: (q) => (q.state.data?.running ? 1500 : false),
  });
  // Bulk lyrics fill: same poll-while-running pattern as the ISRC fill.
  const lyricsFillQ = useQuery({
    queryKey: ["lyrics-fill"],
    queryFn: () => api.get<LyricsFillStatus>("/api/lyrics/fill/status"),
    refetchInterval: (q) => (q.state.data?.running ? 1500 : false),
  });
  async function startLyricsFill() {
    await api.post("/api/lyrics/fill/start", { retry_not_found: lyricsRetryNotFound });
    qc.invalidateQueries({ queryKey: ["lyrics-fill"] });
  }
  async function cancelLyricsFill() {
    await api.post("/api/lyrics/fill/cancel", {});
    qc.invalidateQueries({ queryKey: ["lyrics-fill"] });
  }

  const [applied, setApplied] = useState<Record<string, string>>({});
  async function startFill() {
    await api.post("/api/isrc/fill/start", {});
    setApplied({});
    qc.invalidateQueries({ queryKey: ["isrc-fill"] });
  }
  async function cancelFill() {
    await api.post("/api/isrc/fill/cancel", {});
    qc.invalidateQueries({ queryKey: ["isrc-fill"] });
  }
  const applyIsrc = useMutation({
    mutationFn: (v: { file_path: string; isrc: string }) => api.post("/api/track/isrc", v),
    onSuccess: (_d, v) => {
      setApplied((a) => ({ ...a, [v.file_path]: v.isrc }));
      qc.invalidateQueries({ queryKey: ["duplicates"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
    },
  });
  async function purgeTrash() {
    if (!window.confirm("Permanently delete everything in the trash? This cannot be undone.")) return;
    setPurging(true);
    try { await api.post("/api/trash/purge", {}); qc.invalidateQueries({ queryKey: ["trash"] }); }
    finally { setPurging(false); }
  }
  async function purgeDeleted() {
    const n = deletedQ.data?.count ?? 0;
    if (!window.confirm(
      `Permanently remove ${n} deleted track${n === 1 ? "" : "s"} from the database?\n\n` +
      "These records are for files already gone from your library. This clears the stale " +
      "entries only — no files on disk are touched. This action cannot be undone."
    )) return;
    setPurgingDeleted(true);
    try {
      await api.post("/api/deleted/purge", {});
      qc.invalidateQueries({ queryKey: ["deleted"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
    } finally { setPurgingDeleted(false); }
  }
  const cfg = settingsQ.data?.settings;
  const envLocked = settingsQ.data?.env_locked ?? [];
  const isLocked = (key: string) => envLocked.includes(key);

  // Per-section local state, seeded once settings load.
  const [ntfy, setNtfy] = useState({ url: "", topic: "", batch: 10, interval: 300, notifyReview: true });
  const [scan, setScan] = useState({ workers: 1, bpmMin: 60, bpmMax: 200, useDr: true, useEs: true, writeTags: true, preserveMtime: true, conf: 0.4 });
  const [mode, setMode] = useState("watch");
  const [nav, setNav] = useState({ url: "", user: "", pass: "", starSync: false, scrobble: false });
  const [syncInterval, setSyncInterval] = useState(0);
  const [syncIntSaved, setSyncIntSaved] = useState<Saved>("");
  const [starSyncMsg, setStarSyncMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [starSyncing, setStarSyncing] = useState(false);
  const [playPullMsg, setPlayPullMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [playPulling, setPlayPulling] = useState(false);
  const [playback, setPlayback] = useState(3);
  const [run, setRun] = useState({
    presets: [{ name: "Warmup", bpm: 120 }, { name: "Easy", bpm: 155 },
              { name: "Steady", bpm: 165 }, { name: "Tempo", bpm: 175 }],
    octave: true, preferStarred: true, preferFamiliar: false,
    queueSize: 20, tolerance: 4, stretchLimit: 15, forceTempo: false,
  });
  const [fetchArtistImages, setFetchArtistImages] = useState(false);
  const [artistImagesToLibrary, setArtistImagesToLibrary] = useState(false);
  const [lyricsCfg, setLyricsCfg] = useState({ enabled: false, mode: "embed" });
  const [lyricsRetryNotFound, setLyricsRetryNotFound] = useState(false);
  const [pw, setPw] = useState({ current: "", next: "", confirm: "" });
  const [runPw, setRunPw] = useState({ next: "", confirm: "" });
  const [runSessionDays, setRunSessionDays] = useState(30);
  const [grabber, setGrabber] = useState({
    enabled: false, syncMinutes: 30, publicUrl: "", dryRun: false,
    outputFormat: "mp3-128", pathTemplate: "", providerOrder: "deezer,ytdlp",
    deezerArl: "", deezerQuality: "MP3_128",
    monoUrl: "", monoKey: "", monoQuality: "LOSSLESS",
  });
  const [grabberSaved, setGrabberSaved] = useState<Saved>("");
  const [testMsg, setTestMsg] = useState<Record<string, { ok: boolean; text: string }>>({});

  async function testConn(kind: string, path: string, body: unknown) {
    setTestMsg((m) => ({ ...m, [kind]: { ok: false, text: "Testing…" } }));
    try {
      const r = await api.post<{ ok: boolean; message?: string; error?: string }>(path, body);
      setTestMsg((m) => ({ ...m, [kind]: { ok: !!r.ok, text: r.ok ? (r.message || "OK") : (r.error || "Failed") } }));
    } catch (e) {
      setTestMsg((m) => ({ ...m, [kind]: { ok: false, text: e instanceof Error ? e.message : "Failed" } }));
    }
  }

  async function pullPlayCounts() {
    setPlayPulling(true);
    setPlayPullMsg(null);
    try {
      const r = await api.post<{ ok: boolean; error?: string; remote_songs?: number;
                                 matched?: number; unmatched_remote?: number }>(
        "/api/settings/sync-play-counts");
      if (r.ok) {
        const bits = [`${r.matched ?? 0} of ${r.remote_songs ?? 0} songs matched`];
        if (r.unmatched_remote) bits.push(`${r.unmatched_remote} remote-only`);
        setPlayPullMsg({ ok: true, text: `Play counts pulled — ${bits.join(", ")}` });
      } else {
        setPlayPullMsg({ ok: false, text: r.error || "Pull failed" });
      }
    } catch (e) {
      setPlayPullMsg({ ok: false, text: e instanceof Error ? e.message : "Pull failed" });
    } finally {
      setPlayPulling(false);
    }
  }

  async function syncStars() {
    setStarSyncing(true);
    setStarSyncMsg(null);
    try {
      const r = await api.post<{ ok: boolean; error?: string; pushed?: number; pulled?: number;
                                 conflicts?: number; unmatched_remote?: number; failed?: number }>(
        "/api/settings/sync-stars");
      if (r.ok) {
        const bits = [`pulled ${r.pulled ?? 0}`, `pushed ${r.pushed ?? 0}`];
        if (r.conflicts) bits.push(`${r.conflicts} conflicts`);
        if (r.unmatched_remote) bits.push(`${r.unmatched_remote} remote stars unmatched`);
        if (r.failed) bits.push(`${r.failed} failed (will retry next sync)`);
        setStarSyncMsg({ ok: !r.failed, text: `Stars synced — ${bits.join(", ")}` });
      } else {
        setStarSyncMsg({ ok: false, text: r.error || "Sync failed" });
      }
    } catch (e) {
      setStarSyncMsg({ ok: false, text: e instanceof Error ? e.message : "Sync failed" });
    } finally {
      setStarSyncing(false);
    }
  }

  function previewPath(tpl: string): string {
    const sample: Record<string, string | number> = {
      AlbumArtist: "The Weeknd", Artist: "The Weeknd", Album: "After Hours",
      Title: "Blinding Lights", TrackNo: 3, DiscNo: 1, Year: 2020,
      ext: (grabber.outputFormat.split("-")[0] === "opus" ? "opus" : grabber.outputFormat.split("-")[0]),
    };
    return tpl.replace(/\{(\w+)(?::0(\d)d)?\}/g, (_m, k, pad) => {
      let v = sample[k] ?? "";
      if (pad && typeof v === "number") v = String(v).padStart(+pad, "0");
      return String(v);
    });
  }

  const [ntfySaved, setNtfySaved] = useState<Saved>("");
  const [scanSaved, setScanSaved] = useState<Saved>("");
  const [modeSaved, setModeSaved] = useState<Saved>("");
  const [navSaved, setNavSaved] = useState<Saved>("");
  const [playSaved, setPlaySaved] = useState<Saved>("");
  const [runSaved, setRunSaved] = useState<Saved>("");
  const [artworkSaved, setArtworkSaved] = useState<Saved>("");
  const [lyricsSaved, setLyricsSaved] = useState<Saved>("");
  const [pwSaved, setPwSaved] = useState<Saved>("");
  const [pwErr, setPwErr] = useState("");
  const [runPwSaved, setRunPwSaved] = useState<Saved>("");
  const [runPwErr, setRunPwErr] = useState("");
  const [runSessSaved, setRunSessSaved] = useState<Saved>("");
  const [hashMsg, setHashMsg] = useState("");
  const [reindexMsg, setReindexMsg] = useState("");
  const [versionMsg, setVersionMsg] = useState<{ text: string; color: string } | null>(null);
  const [restartMsg, setRestartMsg] = useState<{ text: string; color: string } | null>(null);

  useEffect(() => {
    if (!cfg) return;
    const s = (k: string, d = "") => (cfg[k] == null ? d : String(cfg[k]));
    const n = (k: string, d: number) => (cfg[k] == null ? d : Number(cfg[k]));
    const b = (k: string, d: boolean) => (cfg[k] == null ? d : Boolean(cfg[k]));
    setNtfy({ url: s("ntfy_url"), topic: s("ntfy_topic"), batch: n("ntfy_batch_size", 10), interval: n("ntfy_min_interval", 300), notifyReview: b("ntfy_notify_review", true) });
    setScan({ workers: n("workers", 1), bpmMin: Math.round(n("bpm_min", 60)), bpmMax: Math.round(n("bpm_max", 200)), useDr: b("use_deeprhythm", true), useEs: b("use_essentia", true), writeTags: b("write_tags", true), preserveMtime: b("preserve_mtime", true), conf: n("review_confidence_threshold", 0.4) });
    setMode(s("mode", "watch") || "watch");
    setNav({ url: s("navidrome_url"), user: s("navidrome_user"), pass: s("navidrome_pass"), starSync: b("navidrome_star_sync", false), scrobble: b("navidrome_scrobble", false) });
    setSyncInterval(n("sync_interval_minutes", 0));
    setPlayback(n("playback_buffer", 3));
    setRunSessionDays(n("run_session_days", 30));
    const presetDefaults = [{ name: "Warmup", bpm: 120 }, { name: "Easy", bpm: 155 },
                            { name: "Steady", bpm: 165 }, { name: "Tempo", bpm: 175 }];
    setRun({
      // Accept both shapes: {name,bpm} dicts and the legacy plain-number list.
      presets: [0, 1, 2, 3].map((i) => {
        const raw = Array.isArray(cfg.run_presets) ? (cfg.run_presets as unknown[])[i] : undefined;
        if (raw && typeof raw === "object") {
          const p = raw as { name?: unknown; bpm?: unknown };
          return { name: String(p.name ?? presetDefaults[i].name), bpm: Number(p.bpm ?? presetDefaults[i].bpm) };
        }
        if (typeof raw === "number") return { name: presetDefaults[i].name, bpm: raw };
        return presetDefaults[i];
      }),
      octave: b("run_octave_fold", true),
      preferStarred: b("run_prefer_starred", true),
      preferFamiliar: b("run_prefer_familiar", false),
      queueSize: n("run_queue_size", 20),
      tolerance: n("run_tolerance_pct", 4),
      stretchLimit: n("run_stretch_limit_pct", 15),
      forceTempo: b("run_force_tempo", false),
    });
    setFetchArtistImages(b("fetch_artist_images", false));
    setArtistImagesToLibrary(b("artist_images_to_library", false));
    setLyricsCfg({ enabled: b("lyrics_enabled", false), mode: s("lyrics_mode", "embed") || "embed" });
    setGrabber({
      enabled: b("grabber_enabled", false), syncMinutes: n("spotify_sync_minutes", 30),
      publicUrl: s("ui_public_url"), dryRun: b("grab_dry_run", false),
      outputFormat: s("output_format") || "mp3-128", pathTemplate: s("path_template"),
      providerOrder: s("provider_order") || "deezer,ytdlp",
      deezerArl: s("deezer_arl"), deezerQuality: s("deezer_quality") || "MP3_128",
      monoUrl: s("monochrome_base_url"), monoKey: s("monochrome_api_key"),
      monoQuality: s("monochrome_quality") || "LOSSLESS",
    });
  }, [cfg]);

  // Surface the ?spotify=... result the OAuth callback redirected back with.
  const spotifyResult = searchParams.get("spotify");
  function clearSpotifyResult() {
    const p = new URLSearchParams(searchParams);
    p.delete("spotify");
    setSearchParams(p, { replace: true });
  }

  async function connectSpotify() {
    try {
      const { url } = await api.get<{ url: string }>("/api/spotify/authorize-url");
      window.location.href = url;
    } catch {
      /* surfaced via status */
    }
  }
  async function disconnectSpotify() {
    await api.post("/api/spotify/disconnect").catch(() => {});
    qc.invalidateQueries({ queryKey: ["grabber-status"] });
  }

  async function saveSection(path: string, body: unknown, setSaved: (s: Saved) => void) {
    setSaved("saving");
    try {
      await api.post(path, body);
      qc.invalidateQueries({ queryKey: ["settings"] });
      setSaved("ok");
      setTimeout(() => setSaved(""), 1800);
    } catch {
      setSaved("err");
    }
  }

  async function changePassword(e: React.FormEvent) {
    e.preventDefault();
    setPwErr("");
    setPwSaved("saving");
    try {
      await api.post("/api/settings/password", { current_password: pw.current, new_password: pw.next, confirm_password: pw.confirm });
      setPwSaved("ok");
      setPw({ current: "", next: "", confirm: "" });
      setTimeout(() => setPwSaved(""), 1800);
    } catch (err) {
      setPwSaved("err");
      setPwErr(err instanceof Error ? err.message : "Failed");
    }
  }

  async function setRunPassword(e: React.FormEvent) {
    e.preventDefault();
    setRunPwErr("");
    setRunPwSaved("saving");
    try {
      await api.post("/api/settings/run-password", { new_password: runPw.next, confirm_password: runPw.confirm });
      setRunPwSaved("ok");
      setRunPw({ next: "", confirm: "" });
      qc.invalidateQueries({ queryKey: ["settings"] });
      setTimeout(() => setRunPwSaved(""), 1800);
    } catch (err) {
      setRunPwSaved("err");
      setRunPwErr(err instanceof Error ? err.message : "Failed");
    }
  }

  async function disableRunPassword() {
    setRunPwErr("");
    try {
      await api.post("/api/settings/run-password", { disable: true });
      setRunPw({ next: "", confirm: "" });
      qc.invalidateQueries({ queryKey: ["settings"] });
    } catch (err) {
      setRunPwErr(err instanceof Error ? err.message : "Failed");
    }
  }

  async function saveRunSession(e: React.FormEvent) {
    e.preventDefault();
    setRunSessSaved("saving");
    try {
      await api.post("/api/settings/run-session", { run_session_days: runSessionDays });
      setRunSessSaved("ok");
      qc.invalidateQueries({ queryKey: ["settings"] });
      setTimeout(() => setRunSessSaved(""), 1800);
    } catch {
      setRunSessSaved("err");
    }
  }

  async function refreshHashes() {
    setHashMsg("Refreshing…");
    try {
      const d = await api.post<{ ok: boolean; updated?: number; missing?: number; error?: string }>("/api/scan/refresh_hashes", {});
      setHashMsg(d.ok ? `Done — ${d.updated} hashes updated, ${d.missing} files not found.` : `Error: ${d.error || "unknown"}`);
    } catch {
      setHashMsg("Network error");
    }
  }

  async function reindexTags() {
    setReindexMsg("Re-reading tags…");
    try {
      const d = await api.post<{ ok: boolean; cleared?: number; error?: string }>("/api/scan/reindex_tags", {});
      setReindexMsg(d.ok
        ? `Re-indexing ${d.cleared} track(s) in the background — refresh Stats shortly to see updated duplicates.`
        : `Error: ${d.error || "unknown"}`);
    } catch {
      setReindexMsg("Network error");
    }
  }

  async function checkLatest() {
    setVersionMsg({ text: "Checking…", color: "var(--muted)" });
    try {
      const d = await api.get<{ latest?: string; error?: string }>("/api/version/check");
      if (d.error) setVersionMsg({ text: `Error: ${d.error}`, color: "var(--err-fg)" });
      else if (!d.latest) setVersionMsg({ text: "No releases published yet", color: "var(--muted)" });
      else if (d.latest === `v${version}`) setVersionMsg({ text: `✓ Up to date (${d.latest})`, color: "var(--ok-fg)" });
      else setVersionMsg({ text: `Latest: ${d.latest} (you have v${version})`, color: "var(--warn-fg)" });
    } catch {
      setVersionMsg({ text: "Network error", color: "var(--err-fg)" });
    }
  }

  const reconnectIv = useRef<number | null>(null);
  // Stop the reconnect poll if the user navigates away mid-restart, otherwise it
  // keeps hitting /api/progress and can fire an unexpected reload later.
  useEffect(() => () => {
    if (reconnectIv.current !== null) window.clearInterval(reconnectIv.current);
  }, []);

  async function restart() {
    if (!window.confirm("Restart the application now?\n\nAny active scan will be stopped. The page will reconnect automatically.")) return;
    setRestartMsg({ text: "Restarting…", color: "var(--warn-fg)" });
    try {
      await api.post("/api/restart", {});
    } catch {
      /* execv may drop the connection before responding — treat as success */
    }
    setRestartMsg({ text: "Reconnecting…", color: "var(--warn-fg)" });
    reconnectIv.current = window.setInterval(async () => {
      try {
        await api.get("/api/progress");
        if (reconnectIv.current !== null) window.clearInterval(reconnectIv.current);
        window.location.reload();
      } catch {
        /* still down */
      }
    }, 1000);
  }

  if (settingsQ.isLoading) return <p style={{ color: "var(--muted)" }}>Loading settings…</p>;

  const fieldLabel = (label: React.ReactNode, hint?: string) => (
    <div>
      <div className="field-row-label">{label}</div>
      {hint && <div className="field-row-hint">{hint}</div>}
    </div>
  );

  return (
    <>
      <PageHeader
        title="Settings"
        subtitle={<>Change behaviour at runtime — saved to <span style={{ fontFamily: "var(--mono)", color: "var(--text)" }}>settings.json</span>.</>}
      />

      <div className="settings-layout">
        <div className="settings-sidebar">
          {SIDEBAR.map(([id, label]) => (
            <a key={id} href={`#${id}`}>
              {label}
            </a>
          ))}
        </div>

        <div>
          {/* Grabber */}
          <div id="sec-grabber" className="settings-card card">
            <div className="settings-card-header">
              <h2>Grabber</h2>
              <p>Spotify playlist sync + downloader. Client ID/secret are set via environment variables.</p>
            </div>
            {spotifyResult && (
              <div
                className="flash"
                style={
                  spotifyResult === "connected"
                    ? { background: "var(--ok-bg)", borderColor: "var(--ok-bd)", color: "var(--ok-fg)" }
                    : { background: "var(--err-bg)", borderColor: "var(--err-bd)", color: "var(--err-fg)" }
                }
                onClick={clearSpotifyResult}
              >
                {spotifyResult === "connected" ? "Spotify connected ✓" : `Spotify connect failed (${spotifyResult})`}
              </div>
            )}
            <div className="settings-fields">
              <div className="field-row">
                {fieldLabel("Enabled", "Turn the grabber subsystem on. Requires a restart to take effect.")}
                <Toggle on={grabber.enabled} onChange={(v) => setGrabber({ ...grabber, enabled: v })} label="Enable grabber" />
              </div>
              <div className="field-row">
                {fieldLabel("Spotify")}
                <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                  {(() => {
                    const sp = grabberStatus.data?.spotify;
                    if (!grabberStatus.data?.enabled)
                      return <span style={{ color: "var(--muted)", fontSize: 13 }}>Enable + restart first</span>;
                    if (!sp?.configured)
                      // overflowWrap: the env-var run is one unbreakable "word"
                      // that otherwise forces sideways scrolling on narrow screens.
                      return <span style={{ color: "var(--warn-fg)", fontSize: 13, overflowWrap: "anywhere" }}>Set SPOTIFY_CLIENT_ID/SECRET/REDIRECT_URI</span>;
                    if (sp.connected)
                      return (
                        <>
                          <span className="chip chip--have">connected</span>
                          <button className="btn btn-danger btn-sm" onClick={disconnectSpotify}>Disconnect</button>
                        </>
                      );
                    return <button className="btn btn-primary btn-sm" onClick={connectSpotify}>Connect Spotify</button>;
                  })()}
                </div>
              </div>
              <div className="field-row">
                {fieldLabel("Sync interval (min)", "How often watched playlists are re-synced in watch mode")}
                <input type="number" min={1} max={1440} value={grabber.syncMinutes}
                       onChange={(e) => setGrabber({ ...grabber, syncMinutes: +e.target.value })} style={{ width: 90 }} />
              </div>
              <div className="field-row">
                {fieldLabel("Dry run", "Match + plan downloads but don't actually download (routes to the inbox)")}
                <Toggle on={grabber.dryRun} onChange={(v) => setGrabber({ ...grabber, dryRun: v })} label="Dry run" />
              </div>
              <div className="field-row">
                {fieldLabel("Output format", "Every download is transcoded to this single format")}
                <select value={grabber.outputFormat} onChange={(e) => setGrabber({ ...grabber, outputFormat: e.target.value })} style={{ fontFamily: "var(--mono)", fontSize: 12 }}>
                  <option value="mp3-128">mp3-128</option>
                  <option value="mp3-320">mp3-320</option>
                  <option value="flac">flac</option>
                  <option value="opus-192">opus-192</option>
                </select>
              </div>
              <div className="field-row">
                {fieldLabel("Path template", "Destination path for downloaded files")}
                <div style={{ width: "100%", maxWidth: 360 }}>
                  <input type="text" value={grabber.pathTemplate} placeholder="{AlbumArtist}/{Album}/{TrackNo:02d} - {Title}.{ext}"
                         onChange={(e) => setGrabber({ ...grabber, pathTemplate: e.target.value })}
                         style={{ width: "100%", fontFamily: "var(--mono)", fontSize: 12 }} />
                  <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
                    → {previewPath(grabber.pathTemplate || "{AlbumArtist}/{Album}/{TrackNo:02d} - {Title}.{ext}")}
                  </div>
                </div>
              </div>
              <div className="field-row">
                {fieldLabel("Provider order", "Comma-separated; tried in order (deezer,ytdlp)")}
                <input type="text" value={grabber.providerOrder} onChange={(e) => setGrabber({ ...grabber, providerOrder: e.target.value })}
                       style={{ maxWidth: 240, width: "100%", fontFamily: "var(--mono)", fontSize: 12 }} />
              </div>
              <div className="field-row">
                {fieldLabel("Deezer ARL", "Your Deezer ARL token (leave blank to use yt-dlp only). Free ARL = 128 kbps full tracks.")}
                <div style={{ display: "flex", flexDirection: "column", gap: 6, width: "100%", maxWidth: 360 }}>
                  <input type="password" value={grabber.deezerArl} placeholder="ARL token"
                         onChange={(e) => setGrabber({ ...grabber, deezerArl: e.target.value })}
                         style={{ width: "100%", fontFamily: "var(--mono)", fontSize: 12 }} />
                  {/* flexWrap: quality select + Test just miss the squeezed
                      field cell around the ~700px layout — fold, don't overflow. */}
                  <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    <select value={grabber.deezerQuality} onChange={(e) => setGrabber({ ...grabber, deezerQuality: e.target.value })} style={{ fontFamily: "var(--mono)", fontSize: 12 }}>
                      <option value="MP3_128">MP3_128 (free)</option>
                      <option value="MP3_320">MP3_320 (paid)</option>
                      <option value="FLAC">FLAC (paid)</option>
                    </select>
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => testConn("deezer", "/api/settings/test-deezer", { deezer_arl: grabber.deezerArl })}>Test</button>
                    {testMsg.deezer && <span style={{ fontSize: 12, color: testMsg.deezer.ok ? "var(--ok-fg)" : "var(--err-fg)" }}>{testMsg.deezer.text}</span>}
                  </div>
                </div>
              </div>
              <div className="field-row">
                {fieldLabel("Monochrome URL", "Self-hosted Tidal proxy — provider currently on hold")}
                <div style={{ display: "flex", flexDirection: "column", gap: 6, width: "100%", maxWidth: 360 }}>
                  <input type="text" value={grabber.monoUrl} placeholder="http://monochrome:8080"
                         onChange={(e) => setGrabber({ ...grabber, monoUrl: e.target.value })}
                         style={{ width: "100%", fontFamily: "var(--mono)", fontSize: 12 }} />
                  <input type="password" value={grabber.monoKey} placeholder="API key"
                         onChange={(e) => setGrabber({ ...grabber, monoKey: e.target.value })}
                         style={{ width: "100%", fontFamily: "var(--mono)", fontSize: 12 }} />
                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <select value={grabber.monoQuality} onChange={(e) => setGrabber({ ...grabber, monoQuality: e.target.value })} style={{ fontFamily: "var(--mono)", fontSize: 12 }}>
                      <option value="LOSSLESS">LOSSLESS</option>
                      <option value="HIGH">HIGH</option>
                      <option value="LOW">LOW</option>
                    </select>
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => testConn("mono", "/api/settings/test-monochrome", { monochrome_base_url: grabber.monoUrl, monochrome_api_key: grabber.monoKey })}>Test</button>
                    {testMsg.mono && <span style={{ fontSize: 12, color: testMsg.mono.ok ? "var(--ok-fg)" : "var(--err-fg)" }}>{testMsg.mono.text}</span>}
                  </div>
                </div>
              </div>
              <div className="field-row">
                {fieldLabel("Public URL", "Base URL used in ntfy click links (e.g. https://bpm.example.com)")}
                <input type="text" value={grabber.publicUrl}
                       onChange={(e) => setGrabber({ ...grabber, publicUrl: e.target.value })}
                       placeholder="https://bpm.example.com"
                       style={{ maxWidth: 340, width: "100%", fontFamily: "var(--mono)", fontSize: 12 }} />
              </div>
              <div>
                <button
                  type="button"
                  className={"btn btn-sm " + (grabberSaved === "ok" ? "btn-ghost" : "btn-primary")}
                  disabled={grabberSaved === "saving"}
                  onClick={() => saveSection("/api/settings/grabber", {
                    grabber_enabled: grabber.enabled,
                    spotify_sync_minutes: grabber.syncMinutes,
                    ui_public_url: grabber.publicUrl,
                    grab_dry_run: grabber.dryRun,
                    output_format: grabber.outputFormat,
                    path_template: grabber.pathTemplate,
                    provider_order: grabber.providerOrder,
                    deezer_arl: grabber.deezerArl,
                    deezer_quality: grabber.deezerQuality,
                    monochrome_base_url: grabber.monoUrl,
                    monochrome_api_key: grabber.monoKey,
                    monochrome_quality: grabber.monoQuality,
                  }, setGrabberSaved)}
                >
                  {grabberSaved === "saving" ? "Saving…" : grabberSaved === "ok" ? "Saved ✓ (restart to apply)" : "Save Grabber Settings"}
                </button>
              </div>
            </div>
          </div>

          {/* Password */}
          <div id="sec-password" className="settings-card card">
            <div className="settings-card-header">
              <h2>Password</h2>
              <p>Change the UI login password.</p>
            </div>
            <form onSubmit={changePassword}>
              <div className="settings-fields">
                <div className="field-row">
                  {fieldLabel("Current password")}
                  <input type="password" required autoComplete="current-password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} style={{ maxWidth: 340, width: "100%" }} />
                </div>
                <div className="field-row">
                  {fieldLabel("New password")}
                  <input type="password" required autoComplete="new-password" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} style={{ maxWidth: 340, width: "100%" }} />
                </div>
                <div className="field-row">
                  {fieldLabel("Confirm new password")}
                  <input type="password" required autoComplete="new-password" value={pw.confirm} onChange={(e) => setPw({ ...pw, confirm: e.target.value })} style={{ maxWidth: 340, width: "100%" }} />
                </div>
                {pwErr && <div style={{ color: "var(--err-fg)", fontSize: 12 }}>{pwErr}</div>}
                <div>
                  <SaveButton state={pwSaved} label="Update Password" />
                </div>
              </div>
            </form>
          </div>

          {/* Player Access — the run-only password */}
          {(() => {
            const runEnabled = cfg?.run_password_hash === "********" || cfg?.run_password === "********";
            return (
              <div id="sec-run-access" className="settings-card card">
                <div className="settings-card-header">
                  <h2>Player Access</h2>
                  <p>
                    Set a second password that logs into a locked-down mode showing <strong>only the Run page</strong> —
                    the tempo player, nothing else. Hand a phone or tablet to someone for a run without exposing your
                    library, settings, or downloads. Leave it unset to keep the single admin login.
                  </p>
                </div>
                <div className="settings-fields">
                  <div style={{ fontSize: 12, color: runEnabled ? "var(--ok-fg)" : "var(--muted)", fontFamily: "var(--mono)" }}>
                    {runEnabled ? "● Player access is enabled" : "○ Player access is off"}
                  </div>
                </div>
                <form onSubmit={setRunPassword} style={{ marginTop: 12 }}>
                  <div className="settings-fields">
                    <div className="field-row">
                      {fieldLabel(runEnabled ? "New run password" : "Run password")}
                      <input type="password" required autoComplete="new-password" value={runPw.next} onChange={(e) => setRunPw({ ...runPw, next: e.target.value })} style={{ maxWidth: 340, width: "100%" }} />
                    </div>
                    <div className="field-row">
                      {fieldLabel("Confirm password")}
                      <input type="password" required autoComplete="new-password" value={runPw.confirm} onChange={(e) => setRunPw({ ...runPw, confirm: e.target.value })} style={{ maxWidth: 340, width: "100%" }} />
                    </div>
                    {runPwErr && <div style={{ color: "var(--err-fg)", fontSize: 12 }}>{runPwErr}</div>}
                    <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                      <SaveButton state={runPwSaved} label={runEnabled ? "Change run password" : "Enable player access"} />
                      {runEnabled && (
                        <button type="button" className="btn btn-sm btn-ghost" onClick={disableRunPassword}>
                          Disable
                        </button>
                      )}
                    </div>
                  </div>
                </form>
                {runEnabled && (
                  <form onSubmit={saveRunSession} style={{ marginTop: 16, borderTop: "1px solid var(--border)", paddingTop: 16 }}>
                    <div className="settings-fields">
                      <div className="field-row">
                        {fieldLabel("Stay signed in for", "days — the run kiosk rarely re-asks (sliding; each use extends it)")}
                        <input
                          type="number" min={1} max={365} value={runSessionDays}
                          onChange={(e) => setRunSessionDays(Math.max(1, Math.min(365, Number(e.target.value) || 1)))}
                          style={{ maxWidth: 120, width: "100%" }}
                        />
                      </div>
                      <div>
                        <SaveButton state={runSessSaved} label="Save session length" ghost />
                      </div>
                    </div>
                  </form>
                )}
                <PlayerUsers />
              </div>
            );
          })()}

          {/* Notifications */}
          <div id="sec-ntfy" className="settings-card card">
            <div className="settings-card-header">
              <h2>Notifications (ntfy)</h2>
              <p>Push notifications for scan progress and review counts.</p>
            </div>
            <form onSubmit={(e) => { e.preventDefault(); saveSection("/api/settings/ntfy", { ntfy_url: ntfy.url, ntfy_topic: ntfy.topic, ntfy_batch_size: ntfy.batch, ntfy_min_interval: ntfy.interval, ntfy_notify_review: ntfy.notifyReview }, setNtfySaved); }}>
              <div className="settings-fields">
                <div className="field-row">
                  {fieldLabel("ntfy server URL")}
                  <input type="text" value={ntfy.url} onChange={(e) => setNtfy({ ...ntfy, url: e.target.value })} placeholder="https://ntfy.sh" style={{ maxWidth: 340, width: "100%", fontFamily: "var(--mono)", fontSize: 12 }} />
                </div>
                <div className="field-row">
                  {fieldLabel("ntfy topic")}
                  <input type="text" value={ntfy.topic} onChange={(e) => setNtfy({ ...ntfy, topic: e.target.value })} placeholder="my-bpm-alerts" style={{ maxWidth: 340, width: "100%", fontFamily: "var(--mono)", fontSize: 12 }} />
                </div>
                <div className="field-row">
                  {fieldLabel("Batch size", "tracks per notification")}
                  <input type="number" min={1} max={100} value={ntfy.batch} onChange={(e) => setNtfy({ ...ntfy, batch: +e.target.value })} style={{ width: 90 }} />
                </div>
                <div className="field-row">
                  {fieldLabel("Min interval (s)", "between batch notifications")}
                  <input type="number" min={10} max={86400} value={ntfy.interval} onChange={(e) => setNtfy({ ...ntfy, interval: +e.target.value })} style={{ width: 100 }} />
                </div>
                <div className="field-row">
                  {fieldLabel("Notify on review", 'include "N need review" in scan summary')}
                  <Toggle on={ntfy.notifyReview} onChange={(v) => setNtfy({ ...ntfy, notifyReview: v })} label="Notify on review" />
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <SaveButton state={ntfySaved} label="Save Notification Settings" />
                  <button type="button" className="btn btn-ghost btn-sm" onClick={() => testConn("ntfy", "/api/settings/test-ntfy", { ntfy_url: ntfy.url, ntfy_topic: ntfy.topic })}>Test</button>
                  {testMsg.ntfy && <span style={{ fontSize: 12, color: testMsg.ntfy.ok ? "var(--ok-fg)" : "var(--err-fg)" }}>{testMsg.ntfy.text}</span>}
                </div>
              </div>
            </form>
          </div>

          {/* Scan Behavior */}
          <div id="sec-scan" className="settings-card card">
            <div className="settings-card-header">
              <h2>Scan Behavior</h2>
              <p>Tune the BPM detector stack.</p>
            </div>
            <form onSubmit={(e) => { e.preventDefault(); saveSection("/api/settings/scan", { workers: scan.workers, bpm_min: scan.bpmMin, bpm_max: scan.bpmMax, use_deeprhythm: scan.useDr, use_essentia: scan.useEs, write_tags: scan.writeTags, preserve_mtime: scan.preserveMtime, review_confidence_threshold: scan.conf }, setScanSaved); }}>
              <div className="settings-fields">
                <div className="field-row">
                  {fieldLabel(<>Workers <span className="badge badge--review" style={{ fontSize: 10, padding: "1px 6px", marginLeft: 4 }}>⚠ Memory</span></>, "Each deeprhythm worker adds ~500 MB RAM")}
                  <input type="number" min={1} max={8} value={scan.workers} onChange={(e) => setScan({ ...scan, workers: +e.target.value })} style={{ width: 70 }} />
                </div>
                <div className="field-row">
                  {fieldLabel("BPM range", "Plausibility window for octave correction")}
                  {/* flexWrap: two fixed 78px inputs just miss the squeezed
                      field cell around the ~700px layout — fold, don't overflow. */}
                  <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                    <input type="number" min={20} max={300} step={1} value={scan.bpmMin} onChange={(e) => setScan({ ...scan, bpmMin: +e.target.value })} style={{ width: 78, textAlign: "center" }} />
                    <span style={{ color: "var(--muted)" }}>—</span>
                    <input type="number" min={20} max={400} step={1} value={scan.bpmMax} onChange={(e) => setScan({ ...scan, bpmMax: +e.target.value })} style={{ width: 78, textAlign: "center" }} />
                  </div>
                </div>
                <div className="field-row">
                  {fieldLabel(<>Use deeprhythm <span className="badge badge--neutral" style={{ fontSize: 10, padding: "1px 6px", marginLeft: 4 }}>+500 MB</span></>, "PyTorch CNN — most accurate")}
                  <Toggle on={scan.useDr} onChange={(v) => setScan({ ...scan, useDr: v })} label="Use deeprhythm" />
                </div>
                <div className="field-row">
                  {fieldLabel("Use essentia", "RhythmExtractor2013 — second neural detector")}
                  <Toggle on={scan.useEs} onChange={(v) => setScan({ ...scan, useEs: v })} label="Use essentia" />
                </div>
                <div className="field-row">
                  {fieldLabel("Write BPM tags", "Write BPM back to audio file metadata")}
                  <Toggle on={scan.writeTags} onChange={(v) => setScan({ ...scan, writeTags: v })} label="Write BPM tags" />
                </div>
                <div className="field-row">
                  {fieldLabel(
                    <>Preserve file date {isLocked("preserve_mtime") && <span className="badge badge--neutral" style={{ fontSize: 10, padding: "1px 6px", marginLeft: 4 }}>🔒 docker-compose</span>}</>,
                    isLocked("preserve_mtime")
                      ? "Locked by the PRESERVE_MTIME environment variable — edit docker-compose to change it"
                      : "Restore the file's modified time after tagging — keeps Navidrome, backups and sort-by-date undisturbed",
                  )}
                  <Toggle on={scan.preserveMtime} disabled={isLocked("preserve_mtime")} onChange={(v) => setScan({ ...scan, preserveMtime: v })} label="Preserve file date" />
                </div>
                <div className="field-row">
                  {fieldLabel("Review threshold", "Confidence below this flags a track for review (0–1)")}
                  <div className="slider-wrap">
                    <input type="range" min={0} max={1} step={0.01} value={scan.conf} onChange={(e) => setScan({ ...scan, conf: +e.target.value })} />
                    <span className="slider-val">{scan.conf.toFixed(2)}</span>
                  </div>
                </div>
                <div>
                  <SaveButton state={scanSaved} label="Save Scan Settings" />
                </div>
              </div>
            </form>
            <div style={{ marginTop: 18, paddingTop: 14, borderTop: "1px solid var(--border)" }}>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>Refresh stored hashes</div>
              <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 10, maxWidth: 480, lineHeight: 1.5 }}>
                If the scanner re-analyzes your whole library after an upgrade, stored file hashes may be stale. Click to recompute them — the next scan will only process files that have actually changed.
              </div>
              <button className="btn btn-ghost btn-sm" type="button" onClick={refreshHashes}>
                Refresh Hashes
              </button>
              <span style={{ marginLeft: 10, fontSize: 13, color: "var(--muted)" }}>{hashMsg}</span>
            </div>
            <div style={{ marginTop: 18, paddingTop: 14, borderTop: "1px solid var(--border)" }}>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>Re-index tags</div>
              <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 10, maxWidth: 480, lineHeight: 1.5 }}>
                Force a full re-read of every file's metadata (title/artist/album/ISRC) into the database. Use this after editing tags outside the app — e.g. adding ISRCs — so duplicate detection and library matching pick them up. A normal scan skips files whose size and modified-time are unchanged.
              </div>
              <button className="btn btn-ghost btn-sm" type="button" onClick={reindexTags}>
                Re-index Tags
              </button>
              <span style={{ marginLeft: 10, fontSize: 13, color: "var(--muted)" }}>{reindexMsg}</span>
            </div>
          </div>

          {/* Operating Mode */}
          <div id="sec-mode" className="settings-card card">
            <div className="settings-card-header">
              <h2>Operating Mode</h2>
              <p>Controls container startup behaviour and what ▶ Start Scan does.</p>
            </div>
            <form onSubmit={(e) => { e.preventDefault(); saveSection("/api/settings/mode", { mode }, setModeSaved); }}>
              <div className="settings-fields">
                <div className="field-row">
                  {fieldLabel("Mode")}
                  <select value={mode} onChange={(e) => setMode(e.target.value)} style={{ fontFamily: "var(--mono)", fontSize: 12 }}>
                    {MODES.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="field-row">
                  <div />
                  <p style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.5, maxWidth: 440 }}>
                    <strong style={{ color: "var(--text)" }}>watch</strong> / <strong style={{ color: "var(--text)" }}>scan_unscanned</strong> — new/changed files only · <strong style={{ color: "var(--text)" }}>watch_all</strong> / <strong style={{ color: "var(--text)" }}>scan_all</strong> — re-analyze everything on <em>every</em> restart · <strong style={{ color: "var(--text)" }}>scan_review</strong> — flagged &amp; error tracks · <strong style={{ color: "var(--text)" }}>report</strong> — write CSV, no analysis
                    {(mode === "watch_all" || mode === "scan_all") && (
                      <>
                        <br />
                        <span style={{ color: "var(--warn-fg)" }}>⚠ This mode re-analyzes your entire library on every container restart. Switch back to <strong>watch</strong> once you're done.</span>
                      </>
                    )}
                  </p>
                </div>
                <div>
                  <SaveButton state={modeSaved} label="Save Mode" ghost />
                </div>
              </div>
            </form>
          </div>

          {/* Navidrome */}
          <div id="sec-navidrome" className="settings-card card">
            <div className="settings-card-header">
              <h2>Navidrome Integration</h2>
              <p>Trigger a library rescan after every scan pass, keep stars in sync both ways, scrobble plays, and pull play counts.</p>
            </div>
            <form onSubmit={(e) => { e.preventDefault(); saveSection("/api/settings/navidrome", { navidrome_url: nav.url, navidrome_user: nav.user, navidrome_pass: nav.pass, navidrome_star_sync: nav.starSync, navidrome_scrobble: nav.scrobble }, setNavSaved); }}>
              <div className="settings-fields">
                <div className="field-row">
                  {fieldLabel("Navidrome URL")}
                  <input type="text" value={nav.url} onChange={(e) => setNav({ ...nav, url: e.target.value })} placeholder="http://navidrome:4533" style={{ maxWidth: 340, width: "100%", fontFamily: "var(--mono)", fontSize: 12 }} />
                </div>
                <div className="field-row">
                  {fieldLabel("Username")}
                  <input type="text" value={nav.user} onChange={(e) => setNav({ ...nav, user: e.target.value })} autoComplete="off" style={{ maxWidth: 280, width: "100%", fontFamily: "var(--mono)", fontSize: 12 }} />
                </div>
                <div className="field-row">
                  {fieldLabel("Password")}
                  <input type="password" value={nav.pass} onChange={(e) => setNav({ ...nav, pass: e.target.value })} autoComplete="off" style={{ maxWidth: 280, width: "100%" }} />
                </div>
                <div className="field-row">
                  {fieldLabel("Two-way star sync", "Reconcile the app's starred tracks with Navidrome's favourites in both directions — stars set here push out, stars set in Navidrome pull in. Manual for now: save, then use Sync stars now.")}
                  <Toggle on={nav.starSync} onChange={(v) => setNav({ ...nav, starSync: v })} label="Enable star sync" />
                </div>
                <div className="field-row">
                  {fieldLabel("Scrobble plays", "Report tracks played in the built-in player (Run mode included) to Navidrome once they pass the halfway mark — play counts and 'last played' stay accurate, and Navidrome forwards to Last.fm/ListenBrainz if you've connected them there.")}
                  <Toggle on={nav.scrobble} onChange={(v) => setNav({ ...nav, scrobble: v })} label="Scrobble plays" />
                </div>
                <div className="field-row" style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
                  {fieldLabel("Background sync", "Minutes between automatic sync passes for playlists (Spotify + Navidrome), stars, and play counts. 0 = off (use the manual buttons only). Applies on the next restart; runs only in watch mode.")}
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <input
                      type="number" min={0} max={1440} step={5} value={syncInterval}
                      onChange={(e) => setSyncInterval(Math.max(0, Math.min(1440, Number(e.target.value) || 0)))}
                      style={{ width: 90, fontFamily: "var(--mono)", textAlign: "center" }}
                    />
                    <span style={{ color: "var(--muted)", fontSize: 13 }}>min</span>
                    <button
                      type="button" className="btn btn-ghost btn-sm"
                      onClick={() => saveSection("/api/settings/sync-interval", { sync_interval_minutes: syncInterval }, setSyncIntSaved)}
                    >
                      {syncIntSaved === "saving" ? "Saving…" : syncIntSaved === "ok" ? "Saved ✓" : syncIntSaved === "err" ? "Error" : "Save interval"}
                    </button>
                  </div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                  <SaveButton state={navSaved} label="Save Navidrome Settings" />
                  <button type="button" className="btn btn-ghost btn-sm" onClick={() => testConn("nav", "/api/settings/test-navidrome", { navidrome_url: nav.url, navidrome_user: nav.user, navidrome_pass: nav.pass })}>Test</button>
                  {testMsg.nav && <span style={{ fontSize: 12, color: testMsg.nav.ok ? "var(--ok-fg)" : "var(--err-fg)" }}>{testMsg.nav.text}</span>}
                  {nav.starSync && (
                    <button type="button" className="btn btn-soft btn-sm" disabled={starSyncing || !nav.url || !nav.user} onClick={syncStars}>
                      {starSyncing ? "Syncing…" : "Sync stars now"}
                    </button>
                  )}
                  {starSyncMsg && <span style={{ fontSize: 12, color: starSyncMsg.ok ? "var(--ok-fg)" : "var(--err-fg)" }}>{starSyncMsg.text}</span>}
                  <button type="button" className="btn btn-soft btn-sm" disabled={playPulling || !nav.url || !nav.user} onClick={pullPlayCounts}>
                    {playPulling ? "Pulling…" : "Pull play counts"}
                  </button>
                  {playPullMsg && <span style={{ fontSize: 12, color: playPullMsg.ok ? "var(--ok-fg)" : "var(--err-fg)" }}>{playPullMsg.text}</span>}
                </div>
              </div>
            </form>
          </div>

          {/* Playback */}
          <div id="sec-playback" className="settings-card card">
            <div className="settings-card-header">
              <h2>Playback</h2>
              <p>Controls audio buffering in the track detail player.</p>
            </div>
            <form onSubmit={(e) => { e.preventDefault(); saveSection("/api/settings/playback", { playback_buffer: playback }, setPlaySaved); }}>
              <div className="field-row">
                {fieldLabel("Buffer before play", "Seconds of audio to buffer before starting playback. Set to 0 to play immediately. Increase if you hear stuttering on slow storage (NAS).")}
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <input type="number" min={0} max={30} step={0.5} value={playback} onChange={(e) => setPlayback(+e.target.value)} style={{ width: 78, fontFamily: "var(--mono)", textAlign: "center" }} />
                  <span style={{ color: "var(--muted)", fontSize: 13 }}>s</span>
                </div>
              </div>
              <div style={{ marginTop: 14 }}>
                <SaveButton state={playSaved} label="Save Playback Settings" />
              </div>
            </form>
          </div>

          {/* Run mode */}
          <div id="sec-run" className="settings-card card">
            <div className="settings-card-header">
              <h2>Run Mode</h2>
              <p>Tempo-locked playback for cadence running — configure the <code>/run</code> page's presets and how the queue is matched.</p>
            </div>
            <form onSubmit={(e) => {
              e.preventDefault();
              saveSection("/api/settings/run", {
                run_presets: run.presets,
                run_octave_fold: run.octave,
                run_prefer_starred: run.preferStarred,
                run_prefer_familiar: run.preferFamiliar,
                run_queue_size: run.queueSize,
                run_tolerance_pct: run.tolerance,
                run_stretch_limit_pct: run.stretchLimit,
                run_force_tempo: run.forceTempo,
              }, setRunSaved);
            }}>
              <div className="field-row">
                {fieldLabel("BPM presets", "Four named one-tap cadence targets shown on the Run page — e.g. your warmup, easy, steady and tempo cadences.")}
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  {run.presets.map((p, i) => (
                    <div key={i} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                      <input type="text" maxLength={20} value={p.name} placeholder="Name" aria-label={`Preset ${i + 1} name`}
                             onChange={(e) => setRun({ ...run, presets: run.presets.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)) })}
                             style={{ width: 86, fontSize: 12, textAlign: "center" }} />
                      <input type="number" min={30} max={300} step={1} value={p.bpm} aria-label={`Preset ${i + 1} BPM`}
                             onChange={(e) => setRun({ ...run, presets: run.presets.map((x, j) => (j === i ? { ...x, bpm: +e.target.value } : x)) })}
                             style={{ width: 86, fontFamily: "var(--mono)", textAlign: "center" }} />
                    </div>
                  ))}
                </div>
              </div>
              <div className="field-row">
                {fieldLabel("Octave matching", "Count half- and double-time tracks as matches: at a 150 cadence a 75 BPM song plays at native speed and you step on every beat. Off = only tracks whose actual BPM is near the target.")}
                <Toggle on={run.octave} onChange={(v) => setRun({ ...run, octave: v })} label="Octave matching" />
              </div>
              <div className="field-row">
                {fieldLabel("Prefer starred tracks", "Fill the queue with starred tracks first, then top up with the closest unstarred matches.")}
                <Toggle on={run.preferStarred} onChange={(v) => setRun({ ...run, preferStarred: v })} label="Prefer starred tracks" />
              </div>
              <div className="field-row">
                {fieldLabel("Prefer familiar tracks", "Among equally-starred matches, pick your most-played tracks first. Uses the play counts pulled from Navidrome (Settings → Navidrome → Pull play counts); tracks with no pulled count sort last.")}
                <Toggle on={run.preferFamiliar} onChange={(v) => setRun({ ...run, preferFamiliar: v })} label="Prefer familiar tracks" />
              </div>
              <div className="field-row">
                {fieldLabel("Queue size", "How many tracks a run queue preloads.")}
                <input type="number" min={1} max={200} step={1} value={run.queueSize}
                       onChange={(e) => setRun({ ...run, queueSize: +e.target.value })}
                       style={{ width: 78, fontFamily: "var(--mono)", textAlign: "center" }} />
              </div>
              <div className="field-row">
                {fieldLabel("Match tolerance", "How far (in %) a track's octave-folded BPM may sit from the target and still be queued. The tempo lock stretches this remainder away during playback.")}
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <input type="number" min={0.5} max={30} step={0.5} value={run.tolerance}
                         onChange={(e) => setRun({ ...run, tolerance: +e.target.value })}
                         style={{ width: 78, fontFamily: "var(--mono)", textAlign: "center" }} />
                  <span style={{ color: "var(--muted)", fontSize: 13 }}>%</span>
                </div>
              </div>
              <div className="field-row">
                {fieldLabel("Play everything (force tempo)", "Default for new runs: ignore the match tolerance entirely and force every track onto the target cadence via the tempo lock (extreme rates are clamped). The Run page has a per-run toggle that overrides this.")}
                <Toggle on={run.forceTempo} onChange={(v) => setRun({ ...run, forceTempo: v })} label="Play everything, force tempo" />
              </div>
              <div className="field-row">
                {fieldLabel("Max stretch", "Upper bound (in %) for how far the tempo lock may speed up or slow down a track. Browser time-stretching starts to sound artificial beyond ~15%.")}
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <input type="number" min={1} max={50} step={1} value={run.stretchLimit}
                         onChange={(e) => setRun({ ...run, stretchLimit: +e.target.value })}
                         style={{ width: 78, fontFamily: "var(--mono)", textAlign: "center" }} />
                  <span style={{ color: "var(--muted)", fontSize: 13 }}>%</span>
                </div>
              </div>
              <div style={{ marginTop: 14 }}>
                <SaveButton state={runSaved} label="Save Run Settings" />
              </div>
            </form>
          </div>

          {/* Artwork */}
          <div id="sec-artwork" className="settings-card card">
            <div className="settings-card-header">
              <h2>Artwork</h2>
              <p>Artist images come from an <code>artist.jpg</code> next to the artist's files when present. Optionally fetch missing ones online.</p>
            </div>
            <form onSubmit={(e) => { e.preventDefault(); saveSection("/api/settings/artwork", { fetch_artist_images: fetchArtistImages, artist_images_to_library: artistImagesToLibrary }, setArtworkSaved); }}>
              <div className="field-row">
                {fieldLabel("Fetch artist images online", "Look up artists without a local artist.jpg on Deezer's public API (no account needed) and cache the image on disk — each artist is fetched at most once a day, and lookups are rate-limited well under Deezer's quota. Sends artist names to Deezer, so it's off by default.")}
                <Toggle on={fetchArtistImages} onChange={setFetchArtistImages} label="Fetch artist images online" />
              </div>
              <div className="field-row">
                {fieldLabel("Save artist images into the library", "Write fetched or hand-picked artist images as artist.jpg in the artist's own folder, so Navidrome and other players see them too. Only folders that exclusively contain that artist's tracks are written to — flat or shared layouts keep using the app cache.")}
                <Toggle on={artistImagesToLibrary} onChange={setArtistImagesToLibrary} label="Save artist images into the library" />
              </div>
              <div style={{ marginTop: 14 }}>
                <SaveButton state={artworkSaved} label="Save Artwork Settings" />
              </div>
            </form>
          </div>

          {/* Lyrics */}
          <div id="sec-lyrics" className="settings-card card">
            <div className="settings-card-header">
              <h2>Lyrics</h2>
              <p>Lyrics come from <a href="https://lrclib.net" target="_blank" rel="noreferrer" style={{ color: "var(--accent-2)" }}>LRCLIB</a> — a free, community-run lyrics database (no account needed). Synced (LRC) lyrics are preferred when available; Navidrome and most players pick them up.</p>
            </div>
            <form onSubmit={(e) => { e.preventDefault(); saveSection("/api/settings/lyrics", { lyrics_enabled: lyricsCfg.enabled, lyrics_mode: lyricsCfg.mode }, setLyricsSaved); }}>
              <div className="field-row">
                {fieldLabel("Auto-fetch for downloaded tracks", "When the Music Grabber downloads a track, also fetch its lyrics from LRCLIB and embed them. Manual fetching from a track's page always works regardless of this toggle.")}
                <Toggle on={lyricsCfg.enabled} onChange={(v) => setLyricsCfg({ ...lyricsCfg, enabled: v })} label="Auto-fetch lyrics for downloaded tracks" />
              </div>
              <div className="field-row">
                {fieldLabel("Storage", "Embed writes the lyrics into the file's tag (travels with the file). Sidecar writes a .lrc text file next to the audio file instead — the file itself is untouched.")}
                <select value={lyricsCfg.mode} onChange={(e) => setLyricsCfg({ ...lyricsCfg, mode: e.target.value })}>
                  <option value="embed">Embed in the file tag</option>
                  <option value="sidecar">.lrc sidecar file</option>
                </select>
              </div>
              <div style={{ marginTop: 14 }}>
                <SaveButton state={lyricsSaved} label="Save Lyrics Settings" />
              </div>
            </form>
            <div style={{ borderTop: "1px solid var(--border)", marginTop: 16, paddingTop: 14 }}>
              <p style={{ fontSize: 12, color: "var(--muted)", marginBottom: 10, lineHeight: 1.5, maxWidth: 500 }}>
                Fetch lyrics for every library track that doesn't have any yet (needs title + artist tags). Tracks already carrying embedded lyrics or a .lrc sidecar are indexed, not re-fetched.
              </p>
              {(() => {
                const f = lyricsFillQ.data;
                return (
                  <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                    <button className="btn btn-primary btn-sm" type="button" disabled={f?.running} onClick={startLyricsFill}>
                      {f?.running ? "Fetching…" : "Fetch missing lyrics"}
                    </button>
                    {f?.running && (
                      <button className="btn btn-ghost btn-sm" type="button" onClick={cancelLyricsFill}>Cancel</button>
                    )}
                    <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--muted)", cursor: "pointer" }}>
                      <input type="checkbox" checked={lyricsRetryNotFound} onChange={(e) => setLyricsRetryNotFound(e.target.checked)} disabled={f?.running} />
                      Retry tracks previously not found
                    </label>
                    {f && (f.running || f.total > 0) && (
                      <span style={{ fontSize: 12, color: "var(--muted)", fontFamily: "var(--mono)" }}>
                        {f.done}/{f.total} checked · {f.filled} filled · {f.not_found} not found
                      </span>
                    )}
                  </div>
                );
              })()}
            </div>
          </div>

          {/* ISRC bulk fill */}
          <div id="sec-isrc" className="settings-card card">
            <div className="settings-card-header">
              <h2>Fill missing ISRCs</h2>
              <p>Look up the ISRC for every library track that's missing one (Deezer / Spotify / MusicBrainz) and write it to the tag. A confident, duration-matched single result is filled automatically; anything uncertain or not found is listed below for you to choose.</p>
            </div>
            {(() => {
              const f = fillQ.data;
              const unresolved = (f?.unresolved ?? []).filter((u) => !applied[u.file_path]);
              return (
                <>
                  <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                    <button className="btn btn-primary btn-sm" type="button" disabled={f?.running} onClick={startFill}>
                      {f?.running ? "Filling…" : "Fill missing ISRCs"}
                    </button>
                    {f?.running && (
                      <button className="btn btn-ghost btn-sm" type="button" onClick={cancelFill}>Cancel</button>
                    )}
                    {f && (f.running || f.total > 0) && (
                      <span style={{ fontSize: 12, color: "var(--muted)", fontFamily: "var(--mono)" }}>
                        {f.done}/{f.total} checked · {f.filled} filled · {(f?.unresolved ?? []).length} to review
                      </span>
                    )}
                  </div>
                  {unresolved.length > 0 && (
                    <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 8 }}>
                      {unresolved.map((u) => (
                        <div key={u.file_path} style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "8px 10px" }}>
                          <div style={{ fontSize: 12, marginBottom: 6 }}>
                            <span style={{ fontWeight: 500 }}>{u.title || u.file_path}</span>
                            <span style={{ color: "var(--muted)" }}> · {u.artist}</span>
                          </div>
                          {u.candidates.length === 0 ? (
                            <span style={{ fontSize: 11, color: "var(--muted)" }}>No candidates found — edit the track's title/artist and retry, or leave it.</span>
                          ) : (
                            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                              {u.candidates.map((c) => (
                                <div key={c.source + c.isrc} style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 11 }}>
                                  <span className="chip chip--neutral">{c.source}</span>
                                  <button className="btn btn-bare btn-sm" style={{ fontFamily: "var(--mono)" }} disabled={applyIsrc.isPending}
                                    onClick={() => applyIsrc.mutate({ file_path: u.file_path, isrc: c.isrc })}>
                                    {c.isrc}
                                  </button>
                                  <span style={{ color: "var(--muted)", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.artist} – {c.title}</span>
                                  {c.url && <a href={c.url} target="_blank" rel="noreferrer" style={{ color: "var(--accent-2)", marginLeft: "auto", flexShrink: 0 }}>open ↗</a>}
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </>
              );
            })()}
          </div>

          {/* Trash */}
          <div id="sec-trash" className="settings-card card">
            <div className="settings-card-header">
              <h2>Trash</h2>
              <p>Files removed via duplicate resolution are moved here and stay recoverable until you purge. Purging deletes them permanently from disk.</p>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
              <span style={{ color: "var(--muted)", fontSize: 13, fontFamily: "var(--mono)" }}>
                {trashQ.isLoading ? "Loading…" : `${trashQ.data?.count ?? 0} file(s) · ${fmtBytes(trashQ.data?.bytes ?? 0)}`}
              </span>
              <button
                className="btn btn-danger btn-sm"
                type="button"
                disabled={purging || !trashQ.data?.count}
                onClick={purgeTrash}
              >
                {purging ? "Purging…" : "Purge trash"}
              </button>
            </div>
          </div>

          {/* Deleted tracks */}
          <div id="sec-deleted" className="settings-card card">
            <div className="settings-card-header">
              <h2>Deleted Tracks</h2>
              <p>Records for files that are no longer in your library (removed from disk, or moved to the trash during duplicate resolution). Purging clears these stale database entries only — it does not touch any files on disk.</p>
            </div>
            <p style={{ color: "var(--muted)", fontSize: 13, marginBottom: 14, lineHeight: 1.5, maxWidth: 460 }}>
              <strong style={{ color: "var(--warn-fg)" }}>This cannot be undone.</strong> The deleted records are dropped permanently. (Files a track pointed at are already gone; recoverable trash copies are managed separately in the Trash section above.)
            </p>
            <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
              <span style={{ color: "var(--muted)", fontSize: 13, fontFamily: "var(--mono)" }}>
                {deletedQ.isLoading ? "Loading…" : `${deletedQ.data?.count ?? 0} deleted record(s)`}
              </span>
              <button
                className="btn btn-danger btn-sm"
                type="button"
                disabled={purgingDeleted || !deletedQ.data?.count}
                onClick={purgeDeleted}
              >
                {purgingDeleted ? "Purging…" : "Purge deleted tracks"}
              </button>
            </div>
          </div>

          {/* Version */}
          <div id="sec-version" className="settings-card card">
            <div className="settings-card-header">
              <h2>Version</h2>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
              <span style={{ color: "var(--muted)", fontSize: 13 }}>Current version</span>
              <span style={{ background: "var(--accent)", color: "white", padding: "3px 10px", borderRadius: 6, fontFamily: "var(--mono)", fontSize: 13, fontWeight: 600 }}>v{version}</span>
              <button className="btn btn-ghost btn-sm" type="button" onClick={checkLatest}>
                Check for latest
              </button>
              {versionMsg && <span style={{ fontSize: 13, color: versionMsg.color }}>{versionMsg.text}</span>}
            </div>
          </div>

          {/* Restart */}
          <div id="sec-restart" className="settings-card card">
            <div className="settings-card-header">
              <h2>Restart</h2>
              <p>Restarts the application process. Re-reads env vars and settings.</p>
            </div>
            <p style={{ color: "var(--muted)", fontSize: 13, marginBottom: 14, lineHeight: 1.5, maxWidth: 460 }}>
              Restarts the application using the same command and environment variables. <strong style={{ color: "var(--warn-fg)" }}>Any active scan will be stopped.</strong> The page will reconnect automatically.
            </p>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <button className="btn btn-danger btn-sm" type="button" onClick={restart}>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M20 12 a8 8 0 1 1 -2.34 -5.66 M20 4 V8 H16" />
                </svg>
                Restart Application
              </button>
              {restartMsg && <span style={{ fontSize: 13, color: restartMsg.color }}>{restartMsg.text}</span>}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
