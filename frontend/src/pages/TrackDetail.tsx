import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiUpload } from "../lib/api";
import { basename, dirname } from "../lib/paths";
import { usePlayer } from "../lib/player";
import type { LyricsResponse, MetadataCandidate, TrackDetailResponse } from "../lib/types";
import { ImagePicker } from "../components/ImagePicker";
import { BpmDisplay } from "../components/BpmDisplay";
import { DetectorBar } from "../components/DetectorBar";
import { useTapTempo } from "../hooks/useTapTempo";
import { useWaveform } from "../hooks/useWaveform";
import { useTitle } from "../hooks/useTitle";
import { fmtTime, useAudioTime } from "../hooks/useAudioTime";
import { Cover, useArtwork } from "../components/Artwork";
import RelatedPanel from "../components/RelatedPanel";

export default function TrackDetail() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const path = params.get("path") || "";
  const qc = useQueryClient();
  useTitle(path ? basename(path) : "Track");

  const detailQ = useQuery({
    queryKey: ["track", path],
    queryFn: () => api.get<TrackDetailResponse>(`/api/track?path=${encodeURIComponent(path)}&back=${params.get("back") || "tracks"}`),
    enabled: !!path,
  });

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const player = usePlayer();
  const [showArt] = useArtwork();
  const active = player.isCurrent(path);          // is this the track loaded in the bar?
  const trackReady = !!detailQ.data?.track;
  const { loading: wfLoading } = useWaveform(canvasRef, player.audioRef, path, trackReady, active);
  const { time, dur } = useAudioTime(player.audioRef);
  const tap = useTapTempo();
  const tapBtnRef = useRef<HTMLButtonElement>(null);

  const [bpmInput, setBpmInput] = useState("");
  const [saveMsg, setSaveMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [unlockMsg, setUnlockMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [reanalyzing, setReanalyzing] = useState(false);
  const [reanalyzeMsg, setReanalyzeMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [metaForm, setMetaForm] = useState({ title: "", artist: "", album: "", album_artist: "", track_no: "", disc_no: "", year: "", isrc: "" });
  const [applyTemplate, setApplyTemplate] = useState(false);
  const [metaMsg, setMetaMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [isrcFinding, setIsrcFinding] = useState(false);
  const [isrcCands, setIsrcCands] = useState<{ source: string; isrc: string; title: string; artist: string; url: string }[] | null>(null);
  const [isrcSpotifyUrl, setIsrcSpotifyUrl] = useState("");
  const [metaFinding, setMetaFinding] = useState(false);
  const [metaCands, setMetaCands] = useState<MetadataCandidate[] | null>(null);
  const [detailCand, setDetailCand] = useState<MetadataCandidate | null>(null);

  // Look up full tag sets from Spotify/Deezer — directly by ISRC when the
  // field is filled, else by artist+title, else by the filename.
  async function findMetadata() {
    setMetaFinding(true);
    setMetaCands(null);
    setIsrcCands(null);
    try {
      const isrc = metaForm.isrc.replace(/[\s-]/g, "");
      const params = isrc
        ? `isrc=${encodeURIComponent(isrc)}`
        : (metaForm.artist.trim() || metaForm.title.trim())
        ? `artist=${encodeURIComponent(metaForm.artist)}&title=${encodeURIComponent(metaForm.title)}`
        : `q=${encodeURIComponent(basename(path).replace(/\.[^.]+$/, ""))}`;
      const r = await api.get<{ candidates: MetadataCandidate[] }>(`/api/metadata/lookup?${params}`);
      setMetaCands(r.candidates || []);
    } catch {
      setMetaCands([]);
    } finally {
      setMetaFinding(false);
    }
  }

  function applyCandidate(c: MetadataCandidate) {
    // Fill from the candidate, keeping the current value where it has none.
    setMetaForm((f) => ({
      title: c.title || f.title,
      artist: c.artist || f.artist,
      album: c.album || f.album,
      album_artist: c.album_artist || f.album_artist,
      track_no: c.track_no != null ? String(c.track_no) : f.track_no,
      disc_no: c.disc_no != null ? String(c.disc_no) : f.disc_no,
      year: c.year != null ? String(c.year) : f.year,
      isrc: c.isrc || f.isrc,
    }));
    setMetaCands(null);
    setDetailCand(null);
    setMetaMsg({ ok: true, text: "Fields filled — review, then Save metadata." });
  }

  function fmtDur(ms: number | null): string {
    if (!ms) return "—";
    return `${Math.floor(ms / 60000)}:${String(Math.round((ms % 60000) / 1000)).padStart(2, "0")}`;
  }

  useEffect(() => {
    if (!detailCand) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setDetailCand(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [detailCand]);

  function durDelta(ms: number | null): string {
    if (!ms || !track?.duration_ms) return "";
    const d = Math.round((ms - track.duration_ms) / 1000);
    return d === 0 ? " · Δ0s" : ` · Δ${d > 0 ? "+" : ""}${d}s`;
  }
  async function findIsrc() {
    setIsrcFinding(true);
    setIsrcCands(null);
    try {
      const r = await api.get<{ candidates: typeof isrcCands; spotify_search_url: string }>(
        `/api/isrc/lookup?artist=${encodeURIComponent(metaForm.artist)}&title=${encodeURIComponent(metaForm.title)}`);
      setIsrcCands(r.candidates || []);
      setIsrcSpotifyUrl(r.spotify_search_url);
    } finally {
      setIsrcFinding(false);
    }
  }

  const detail = detailQ.data;
  const track = detail?.track;

  // ── Cover editing ──────────────────────────────────────────────────────────
  const [coverPickerOpen, setCoverPickerOpen] = useState(false);
  const [coverV, setCoverV] = useState(0);

  async function applyCover(pick: { url?: string; file?: File }) {
    if (pick.url) await api.post("/api/track/cover", { file_path: path, url: pick.url });
    else if (pick.file) await apiUpload(`/api/track/cover?path=${encodeURIComponent(path)}`, pick.file);
    setCoverV(Date.now());
    setCoverPickerOpen(false);
  }

  // ── Lyrics ─────────────────────────────────────────────────────────────────
  const lyricsQ = useQuery({
    queryKey: ["lyrics", path],
    queryFn: () => api.get<LyricsResponse>(`/api/track/lyrics?path=${encodeURIComponent(path)}`),
    enabled: !!path,
  });
  const [lyricsEditing, setLyricsEditing] = useState(false);
  const [lyricsDraft, setLyricsDraft] = useState("");
  const [lyricsBusy, setLyricsBusy] = useState(false);
  const [lyricsMsg, setLyricsMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function fetchLyrics() {
    setLyricsBusy(true);
    setLyricsMsg(null);
    try {
      const r = await api.post<{ ok: boolean; error?: string; status?: string }>(
        "/api/track/lyrics/fetch", { file_path: path });
      if (r.ok) {
        setLyricsMsg({ ok: true, text: r.status === "instrumental" ? "Marked as instrumental." : "Lyrics fetched ✓" });
        qc.invalidateQueries({ queryKey: ["lyrics", path] });
        qc.invalidateQueries({ queryKey: ["track", path] });
      } else {
        setLyricsMsg({ ok: false, text: r.error || "No lyrics found." });
      }
    } catch (e) {
      setLyricsMsg({ ok: false, text: e instanceof Error ? e.message : "Request failed" });
    } finally {
      setLyricsBusy(false);
    }
  }

  async function saveLyrics(text: string) {
    setLyricsBusy(true);
    setLyricsMsg(null);
    try {
      await api.put("/api/track/lyrics", { file_path: path, lyrics: text });
      setLyricsEditing(false);
      setLyricsMsg({ ok: true, text: text ? "Lyrics saved ✓" : "Lyrics removed." });
      qc.invalidateQueries({ queryKey: ["lyrics", path] });
      qc.invalidateQueries({ queryKey: ["track", path] });
    } catch (e) {
      setLyricsMsg({ ok: false, text: e instanceof Error ? e.message : "Request failed" });
    } finally {
      setLyricsBusy(false);
    }
  }

  // Seed the BPM override input + metadata form when the track loads/changes.
  useEffect(() => {
    setBpmInput(track?.bpm ? track.bpm.toFixed(1) : "");
    setSaveMsg(null);
    setUnlockMsg(null);
    setReanalyzeMsg(null);
    setMetaMsg(null);
    setMetaCands(null);
    setDetailCand(null);
    setLyricsMsg(null);
    setLyricsEditing(false);
    if (track) {
      const s = (v: unknown) => (v == null ? "" : String(v));
      setMetaForm({ title: s(track.title), artist: s(track.artist), album: s(track.album),
        album_artist: s(track.album_artist), track_no: s(track.track_no), disc_no: s(track.disc_no),
        year: s(track.year), isrc: s(track.isrc) });
    }
    // Seed only when the track itself changes. Keying on bpm too would let a
    // background refetch (interval/focus) overwrite the user's in-progress edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [track?.file_path]);

  async function saveMeta() {
    setMetaMsg(null);
    try {
      const res = await api.put<{ ok: boolean; file_path?: string; error?: string }>(
        "/api/track/tags", { file_path: path, ...metaForm, apply_template: applyTemplate });
      if (!res.ok) { setMetaMsg({ ok: false, text: res.error || "Failed" }); return; }
      qc.invalidateQueries({ queryKey: ["tracks"] });
      if (res.file_path && res.file_path !== path) {
        // Keep every back_* param so the Back link still points at the origin
        // page (artist/album/filtered list) after a rename moved the file.
        const sp = new URLSearchParams(params);
        sp.set("path", res.file_path);
        navigate(`/track?${sp.toString()}`, { replace: true });
      } else {
        qc.invalidateQueries({ queryKey: ["track", path] });
        setMetaMsg({ ok: true, text: "Saved" });
      }
    } catch (e) {
      setMetaMsg({ ok: false, text: e instanceof Error ? e.message : "Request failed" });
    }
  }

  // Playback is driven by the shared footer player so it survives navigation.
  // Playing here is a *preview*: it ducks/pauses the queue and resumes it when
  // the preview ends or we leave this page.
  const isPlaying = active && player.playing;
  function togglePlay() {
    if (active) player.toggle();
    else if (track) player.preview({ path, title: basename(track.file_path), artist: track.artist || "" });
  }
  const { endPreview } = player;
  useEffect(() => () => endPreview(), [endPreview]);  // leaving resumes the queue (dec 8)

  function spawnRipple() {
    const btn = tapBtnRef.current;
    if (!btn) return;
    const ring = document.createElement("span");
    ring.className = "tap-ripple";
    btn.appendChild(ring);
    window.setTimeout(() => ring.remove(), 650);
  }

  function applyTap() {
    if (tap.canApply) setBpmInput(parseFloat(tap.display).toFixed(1));
  }

  async function saveBpm() {
    const bpm = parseFloat(bpmInput);
    if (isNaN(bpm) || bpm < 20 || bpm > 300) {
      setSaveMsg({ ok: false, text: "Enter a valid BPM (20–300)." });
      return;
    }
    try {
      const res = await api.post<{ ok: boolean; error?: string }>("/api/save_bpm", { file_path: path, bpm });
      if (res.ok) {
        setSaveMsg({ ok: true, text: `Saved & locked at ${bpm.toFixed(1)} BPM` });
        qc.invalidateQueries({ queryKey: ["track", path] });
        // The player bar + Run page read this key — a live tempo lock re-stretches
        // onto the corrected BPM as soon as it refetches.
        qc.invalidateQueries({ queryKey: ["track-bpm", path] });
        qc.invalidateQueries({ queryKey: ["tracks"] });
        qc.invalidateQueries({ queryKey: ["me"] });
      } else {
        setSaveMsg({ ok: false, text: `Error: ${res.error || "unknown"}` });
      }
    } catch {
      setSaveMsg({ ok: false, text: "Request failed." });
    }
  }

  async function unlockTrack() {
    try {
      const res = await api.post<{ ok: boolean; error?: string }>("/api/unlock", { file_path: path });
      if (res.ok) {
        setUnlockMsg({ ok: true, text: "Unlocked — will be re-analyzed on next scan." });
        qc.invalidateQueries({ queryKey: ["track", path] });
        qc.invalidateQueries({ queryKey: ["tracks"] });
      } else {
        setUnlockMsg({ ok: false, text: `Error: ${res.error || "unknown"}` });
      }
    } catch {
      setUnlockMsg({ ok: false, text: "Request failed." });
    }
  }

  async function reanalyze() {
    setReanalyzing(true);
    setReanalyzeMsg(null);
    try {
      const res = await api.post<{ ok: boolean; error?: string }>("/api/scan/reanalyze", { file_path: path });
      if (res.ok) {
        setReanalyzeMsg({ ok: true, text: "Done — refreshing…" });
        setTimeout(() => {
          qc.invalidateQueries({ queryKey: ["track", path] });
          qc.invalidateQueries({ queryKey: ["tracks"] });
          setReanalyzing(false);
          setReanalyzeMsg(null);
        }, 600);
      } else {
        setReanalyzing(false);
        setReanalyzeMsg({ ok: false, text: `Error: ${res.error || "unknown"}` });
      }
    } catch {
      setReanalyzing(false);
      setReanalyzeMsg({ ok: false, text: "Request failed." });
    }
  }

  if (!path) return <p style={{ color: "var(--muted)" }}>No track selected.</p>;
  if (detailQ.isLoading) return <p style={{ color: "var(--muted)" }}>Loading…</p>;
  if (detailQ.isError || !track) return <p style={{ color: "var(--err-fg)" }}>Track not found.</p>;

  // "artist"/"album" origins are resolved client-side from back_* params (the
  // API only distinguishes review vs tracks, for review-queue navigation).
  const backParam = params.get("back") || "tracks";
  const back = detail!.back;
  const backUrl =
    back === "review"
      ? "/review"
      : backParam === "artist" && params.get("back_name")
      ? `/artist?name=${encodeURIComponent(params.get("back_name")!)}`
      : backParam === "album" && params.get("back_album")
      ? `/album?album=${encodeURIComponent(params.get("back_album")!)}&album_artist=${encodeURIComponent(params.get("back_artist") || "")}`
      : (() => {
          const sp = new URLSearchParams();
          const map: [string, string][] = [
            ["back_filter", "filter"],
            ["back_page", "page"],
            ["back_q", "q"],
            ["back_per_page", "per_page"],
          ];
          for (const [from, to] of map) {
            const v = params.get(from);
            if (v) sp.set(to, v);
          }
          const qs = sp.toString();
          return qs ? `/tracks?${qs}` : "/tracks";
        })();

  const fname = basename(track.file_path);
  const fdir = dirname(track.file_path);
  const beatMs = track.bpm ? Math.round(60000 / track.bpm) : 500;
  const flagged = track.needs_review && !track.reviewed;

  function detailLink(p: string) {
    return `/track?path=${encodeURIComponent(p)}&back=review`;
  }

  return (
    <>
      {/* Top bar */}
      <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 20, flexWrap: "wrap" }}>
        <Link to={backUrl} className="btn btn-bare btn-sm" style={{ paddingLeft: 0 }}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
            <path d="M19 12 H5 M11 6 L5 12 L11 18" />
          </svg>
          Back
        </Link>
        {back === "review" && detail!.queue_total ? (
          <>
            <div style={{ width: 1, height: 18, background: "var(--border)", flexShrink: 0 }} />
            <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)" }}>
              Review queue · <span style={{ color: "var(--text)" }}>{detail!.queue_pos} / {detail!.queue_total}</span>
            </span>
            <div style={{ flex: 1 }} />
            {detail!.prev_path && (
              <Link to={detailLink(detail!.prev_path)} className="btn btn-ghost btn-sm">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
                  <polygon points="19,4 7,12 19,20" />
                  <rect x="5" y="4" width="2" height="16" />
                </svg>
                Prev
              </Link>
            )}
            {detail!.next_path && (
              <Link to={detailLink(detail!.next_path)} className="btn btn-primary btn-sm">
                Next
                <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
                  <polygon points="5,4 17,12 5,20" />
                  <rect x="17" y="4" width="2" height="16" />
                </svg>
              </Link>
            )}
          </>
        ) : (
          <div style={{ flex: 1 }} />
        )}
      </div>

      {/* Header */}
      <div style={{ marginBottom: 22, display: "flex", alignItems: "flex-start", gap: 16 }}>
        {showArt && (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4, flexShrink: 0 }}>
            <Cover path={track.file_path} size={88} v={coverV} />
            <button className="btn btn-bare btn-sm" style={{ fontSize: 10, padding: "2px 6px" }} onClick={() => setCoverPickerOpen(true)}>
              Edit cover
            </button>
          </div>
        )}
        <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
          {track.locked ? (
            <span className="badge badge--locked">
              <span className="badge-dot" />
              locked
            </span>
          ) : null}
          {flagged ? (
            <span className="badge badge--review">
              <span className="badge-dot" />
              needs review
            </span>
          ) : null}
          {track.reviewed ? (
            <span className="badge badge--reviewed">
              <span className="badge-dot" />
              reviewed
            </span>
          ) : null}
          {track.status === "error" ? (
            <span className="badge badge--error">
              <span className="badge-dot" />
              error
            </span>
          ) : null}
          {!track.locked && !flagged && !track.reviewed && track.status !== "error" ? <span className="badge badge--ok">ok</span> : null}
          <button
            className="btn btn-bare btn-sm"
            style={{ padding: "2px 6px", color: track.starred ? "var(--warn-fg)" : "var(--muted)" }}
            aria-pressed={!!track.starred}
            aria-label={track.starred ? "Unstar" : "Star"}
            title={track.starred ? "Unstar" : "Star — preferred when building run queues"}
            onClick={async () => {
              await api.post("/api/track/star", { path: track.file_path, starred: !track.starred });
              qc.invalidateQueries({ queryKey: ["track", path] });
              qc.invalidateQueries({ queryKey: ["tracks"] });
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill={track.starred ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round">
              <polygon points="12,2.5 15,9 22,9.8 17,14.6 18.2,21.6 12,18.2 5.8,21.6 7,14.6 2,9.8 9,9" />
            </svg>
          </button>
          <button
            className="btn btn-bare btn-sm"
            style={{ padding: "2px 6px", color: track.disliked ? "var(--err-fg)" : "var(--muted)" }}
            aria-pressed={!!track.disliked}
            aria-label={track.disliked ? "Remove dislike" : "Dislike"}
            title={track.disliked ? "Remove dislike — eligible for run queues again" : "Dislike — never picked for a run again"}
            onClick={async () => {
              await api.post("/api/track/dislike", { path: track.file_path, disliked: !track.disliked });
              qc.invalidateQueries({ queryKey: ["track", path] });
              qc.invalidateQueries({ queryKey: ["tracks"] });
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill={track.disliked ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17" />
            </svg>
          </button>
          <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)", marginLeft: "auto" }}>
            {track.analyzed_at ? track.analyzed_at.slice(0, 16).replace("T", " ") + " UTC" : ""}
          </span>
        </div>
        <h1 style={{ fontSize: 22, fontWeight: 600, letterSpacing: "-0.015em", marginBottom: 4, wordBreak: "break-word" }}>{fname}</h1>
        {track.artist && (
          <div style={{ fontSize: 13, marginBottom: 4 }}>
            by <Link to={`/artist?name=${encodeURIComponent(track.artist)}`} style={{ color: "var(--accent-2)" }}>{track.artist}</Link>
          </div>
        )}
        <div style={{ fontSize: 12, color: "var(--muted)", fontFamily: "var(--mono)", display: "flex", alignItems: "center", gap: 6 }}>
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round">
            <path d="M3 7 a2 2 0 0 1 2 -2 h4 l2 2 h8 a2 2 0 0 1 2 2 v9 a2 2 0 0 1 -2 2 H5 a2 2 0 0 1 -2 -2 Z" />
          </svg>
          {fdir}
        </div>
        </div>
      </div>

      <div className="track-layout">
        {/* Left column */}
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          {/* Playback */}
          <div className="card">
            <div className="section-label">
              <span>Playback</span>
              <span className="section-hint">{track.bpm ? `beat every ${Math.round(60000 / track.bpm)}ms` : ""}</span>
            </div>
            <div className="waveform-wrap">
              <canvas ref={canvasRef} id="waveform-canvas" />
              {wfLoading && <div id="waveform-loading" />}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
              <button
                onClick={togglePlay}
                style={{ width: 44, height: 44, borderRadius: 999, background: "var(--accent)", color: "white", border: "none", cursor: "pointer", display: "grid", placeItems: "center", flexShrink: 0, boxShadow: "0 4px 16px var(--accent-glow)" }}
                aria-label={isPlaying ? "Pause" : "Play"}
              >
                {isPlaying ? (
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="white">
                    <rect x="6" y="4" width="4" height="16" rx="1" />
                    <rect x="14" y="4" width="4" height="16" rx="1" />
                  </svg>
                ) : (
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="white">
                    <polygon points="6,4 20,12 6,20" />
                  </svg>
                )}
              </button>
              <div style={{ flex: 1, fontFamily: "var(--mono)", fontSize: 12, color: "var(--muted)" }}>
                <span style={{ color: "var(--text)" }}>{fmtTime(active ? time : 0)}</span>
                <span> / {fmtTime(active ? dur : (track.duration_ms ? track.duration_ms / 1000 : 0))}</span>
              </div>
            </div>
          </div>

          {/* Analysis */}
          <div className="card">
            <div className="section-label">
              <span>Analysis</span>
            </div>
            <div className="analysis-inner">
              <div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.1em", color: "var(--muted)", marginBottom: 6 }}>Final BPM</div>
                <BpmDisplay bpm={track.bpm} sizePx={84} pulsing beatMs={beatMs} />
              </div>
              <div style={{ flex: 1, paddingLeft: 32, borderLeft: "1px solid var(--border)" }}>
                <div className="detector-grid">
                  {[
                    { label: "deeprhythm", color: "var(--accent)", val: track.bpm_dr },
                    { label: "essentia", color: "var(--accent-2)", val: track.bpm_es },
                    { label: "librosa", color: "var(--info-fg)", val: track.bpm_lb },
                    { label: "confidence", color: "var(--ok-fg)", val: track.bpm_confidence, conf: true },
                  ].map((d) => (
                    <div className="detector-row" key={d.label}>
                      <span className="detector-row-label">
                        <span className="detector-dot" style={{ background: d.color }} />
                        {d.label}
                      </span>
                      <span className="detector-row-value" style={{ color: d.val != null ? "var(--text)" : "var(--muted)" }}>
                        {d.val != null ? (d.conf ? d.val.toFixed(2) : d.val.toFixed(1)) : "—"}
                      </span>
                    </div>
                  ))}
                </div>
                <div style={{ marginTop: 14, fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)" }}>
                  detector · <span style={{ color: "var(--text)" }}>{track.detector || "—"}</span>
                  {track.play_count != null && (
                    <span style={{ marginLeft: 14 }}>plays · <span style={{ color: "var(--text)" }}>{track.play_count}</span></span>
                  )}
                </div>
              </div>
            </div>
            {track.status !== "error" && <DetectorBar track={track} />}
          </div>

          {/* Override */}
          <div className="card">
            <div className="section-label">
              <span>Override</span>
            </div>
            <p style={{ fontSize: 12, color: "var(--muted)", marginBottom: 14, maxWidth: 460, lineHeight: 1.5 }}>
              Manually set the BPM and lock the track so future scans won't overwrite it. Tap-tempo result feeds in via{" "}
              <span style={{ fontFamily: "var(--mono)", color: "var(--text)" }}>Apply →</span>.
            </p>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <input
                type="number"
                step="0.1"
                min="20"
                max="300"
                value={bpmInput}
                onChange={(e) => setBpmInput(e.target.value)}
                placeholder="BPM"
                style={{ width: 110, padding: "10px 12px", fontFamily: "var(--mono)", fontSize: 18, fontWeight: 600, textAlign: "center", fontVariantNumeric: "tabular-nums" }}
              />
              <button className="btn btn-primary btn-md" onClick={saveBpm}>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round">
                  <rect x="5" y="10" width="14" height="10" rx="2" />
                  <path d="M8 10 V7 a4 4 0 0 1 8 0 V10" />
                </svg>
                Save &amp; lock
              </button>
              {track.locked && (
                <button className="btn btn-danger btn-md" onClick={unlockTrack}>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round">
                    <rect x="5" y="10" width="14" height="10" rx="2" />
                    <path d="M8 10 V7 a4 4 0 0 1 8 0" />
                  </svg>
                  Unlock
                </button>
              )}
              <div style={{ flex: 1 }} />
              <div style={{ fontSize: 12, minHeight: "1.4em", display: "flex", alignItems: "center", gap: 6, color: saveMsg ? (saveMsg.ok ? "var(--ok-fg)" : "var(--err-fg)") : undefined }}>
                {saveMsg?.text}
              </div>
            </div>
            {unlockMsg && <div style={{ fontSize: 12, color: unlockMsg.ok ? "var(--ok-fg)" : "var(--err-fg)", marginTop: 8 }}>{unlockMsg.text}</div>}
            <div style={{ marginTop: 18, paddingTop: 14, borderTop: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <button className="btn btn-ghost btn-md" onClick={reanalyze} disabled={reanalyzing}>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={reanalyzing ? { transformOrigin: "center", animation: "spin 0.8s linear infinite" } : undefined}>
                  <path d="M1 4 v6 h6" />
                  <path d="M3.51 15 a9 9 0 1 0 .49 -4.95" />
                </svg>
                {reanalyzing ? "Analyzing…" : "Re-analyze"}
              </button>
              {reanalyzeMsg && <div style={{ fontSize: 12, color: reanalyzeMsg.ok ? "var(--ok-fg)" : "var(--err-fg)", display: "flex", alignItems: "center", gap: 6 }}>{reanalyzeMsg.text}</div>}
            </div>
          </div>

          {/* Metadata editor */}
          <div className="card">
            <div className="section-label"><span>Metadata</span></div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              {([
                ["title", "Title"], ["artist", "Artist"], ["album", "Album"], ["album_artist", "Album artist"],
                ["track_no", "Track #"], ["disc_no", "Disc #"], ["year", "Year"], ["isrc", "ISRC"],
              ] as [keyof typeof metaForm, string][]).map(([key, label]) => (
                <label key={key} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  <span style={{ fontSize: 10, fontFamily: "var(--mono)", textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--muted)" }}>{label}</span>
                  <input type="text" value={metaForm[key]} onChange={(e) => setMetaForm({ ...metaForm, [key]: e.target.value })} style={{ width: "100%" }} />
                </label>
              ))}
            </div>

            {/* Find metadata / Find ISRC */}
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginTop: 10 }}>
              <button className="btn btn-ghost btn-sm" disabled={metaFinding} onClick={findMetadata}>
                {metaFinding ? "Searching…" : "Find metadata"}
              </button>
              <button className="btn btn-ghost btn-sm" disabled={isrcFinding} onClick={findIsrc}>
                {isrcFinding ? "Finding ISRC…" : "Find ISRC"}
              </button>
              <span style={{ fontSize: 11, color: "var(--muted)" }}>
                Find metadata fills every field from Spotify/Deezer — by ISRC when set, else by artist + title (or the filename). Find ISRC looks up just the ISRC.
              </span>
            </div>
            {metaCands != null && (
              <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "8px 10px", marginTop: 8, fontSize: 11, display: "flex", flexDirection: "column", gap: 8 }}>
                {metaCands.length === 0 ? (
                  <span style={{ color: "var(--muted)" }}>
                    No matches — refine the artist/title (or check the ISRC) and search again.
                  </span>
                ) : (
                  metaCands.map((c) => (
                    <div key={c.source + (c.url || c.isrc || c.title)} style={{ display: "flex", gap: 10, alignItems: "center" }}>
                      <button
                        onClick={() => setDetailCand(c)}
                        title="Show full details"
                        style={{ display: "flex", gap: 10, alignItems: "center", flex: 1, minWidth: 0, background: "none", border: "none", padding: 0, cursor: "pointer", textAlign: "left", color: "inherit", font: "inherit" }}
                      >
                        {c.cover_url ? (
                          <img src={c.cover_url} alt="" loading="lazy" style={{ width: 36, height: 36, borderRadius: 6, objectFit: "cover", flexShrink: 0 }} />
                        ) : (
                          <div className="art-thumb" style={{ width: 36, height: 36, fontSize: 13 }} aria-hidden>♪</div>
                        )}
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            <span style={{ color: "var(--text)", fontWeight: 500 }}>{c.title}</span>
                            <span style={{ color: "var(--muted)" }}> — {c.artist}</span>
                          </div>
                          <div style={{ color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {c.album}
                            {c.year ? ` (${c.year})` : ""}
                            {c.track_no != null ? ` · #${c.track_no}` : ""}
                            {c.duration_ms ? ` · ${fmtDur(c.duration_ms)}${durDelta(c.duration_ms)}` : ""}
                            {c.isrc ? ` · ${c.isrc}` : ""}
                          </div>
                        </div>
                        <span aria-hidden style={{ color: "var(--muted)", flexShrink: 0 }}>›</span>
                      </button>
                      <span className="chip chip--neutral" style={{ flexShrink: 0 }}>{c.source}</span>
                      <button className="btn btn-primary btn-sm" style={{ flexShrink: 0 }} onClick={() => applyCandidate(c)}>Use</button>
                    </div>
                  ))
                )}
              </div>
            )}
            {isrcCands != null && (
              <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "8px 10px", marginTop: 8, fontSize: 11, display: "flex", flexDirection: "column", gap: 6 }}>
                {isrcCands.length === 0 ? (
                  <span style={{ color: "var(--muted)" }}>No ISRC found — refine the title/artist and search again, or open Spotify below.</span>
                ) : (
                  isrcCands.map((c) => (
                    <div key={c.source + c.isrc} style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <span className="chip chip--neutral">{c.source}</span>
                      <button className="btn btn-bare btn-sm" style={{ fontFamily: "var(--mono)" }} title="Use this ISRC" onClick={() => { setMetaForm({ ...metaForm, isrc: c.isrc }); setIsrcCands(null); }}>
                        {c.isrc}
                      </button>
                      <span style={{ color: "var(--muted)", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.artist} – {c.title}</span>
                      {c.url && <a href={c.url} target="_blank" rel="noreferrer" style={{ color: "var(--accent-2)", marginLeft: "auto", flexShrink: 0 }}>open ↗</a>}
                    </div>
                  ))
                )}
                {isrcSpotifyUrl && <a href={isrcSpotifyUrl} target="_blank" rel="noreferrer" style={{ color: "var(--accent-2)" }}>Open Spotify web player search ↗</a>}
              </div>
            )}
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 14, flexWrap: "wrap" }}>
              <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--muted)", cursor: "pointer" }}>
                <input type="checkbox" checked={applyTemplate} onChange={(e) => setApplyTemplate(e.target.checked)} />
                Rename file to path template
              </label>
              <div style={{ flex: 1 }} />
              {metaMsg && <span style={{ fontSize: 12, color: metaMsg.ok ? "var(--ok-fg)" : "var(--err-fg)" }}>{metaMsg.text}</span>}
              <button className="btn btn-primary btn-md" onClick={saveMeta}>Save metadata</button>
            </div>
          </div>

          {/* Lyrics */}
          <div className="card">
            <div className="section-label">
              <span>Lyrics</span>
              <span className="section-hint">
                {lyricsQ.data?.lyrics
                  ? `${lyricsQ.data.synced ? "synced (LRC)" : "plain"} · ${lyricsQ.data.source}`
                  : lyricsQ.data?.status === "instrumental"
                  ? "instrumental"
                  : ""}
              </span>
            </div>
            {lyricsEditing ? (
              <>
                <textarea
                  value={lyricsDraft}
                  onChange={(e) => setLyricsDraft(e.target.value)}
                  rows={12}
                  spellCheck={false}
                  style={{ width: "100%", fontFamily: "var(--mono)", fontSize: 12, lineHeight: 1.6, resize: "vertical" }}
                  placeholder={"Paste lyrics here — plain text or LRC ([mm:ss.xx] lines) for synced lyrics."}
                />
                <div style={{ display: "flex", gap: 8, marginTop: 10, alignItems: "center", flexWrap: "wrap" }}>
                  <button className="btn btn-primary btn-sm" disabled={lyricsBusy} onClick={() => saveLyrics(lyricsDraft)}>Save lyrics</button>
                  <button className="btn btn-ghost btn-sm" disabled={lyricsBusy} onClick={() => setLyricsEditing(false)}>Cancel</button>
                  <div style={{ flex: 1 }} />
                  {lyricsMsg && <span style={{ fontSize: 12, color: lyricsMsg.ok ? "var(--ok-fg)" : "var(--err-fg)" }}>{lyricsMsg.text}</span>}
                </div>
              </>
            ) : (
              <>
                {lyricsQ.data?.lyrics ? (
                  <div style={{ maxHeight: 280, overflowY: "auto", whiteSpace: "pre-wrap", fontSize: 12.5, lineHeight: 1.7, color: "var(--text)", border: "1px solid var(--border)", borderRadius: 8, padding: "10px 12px", marginBottom: 12 }}>
                    {lyricsQ.data.lyrics}
                  </div>
                ) : (
                  <p style={{ fontSize: 12, color: "var(--muted)", marginBottom: 12, lineHeight: 1.5 }}>
                    {lyricsQ.isLoading ? "Loading…" : "No lyrics on this track yet. Fetch them from LRCLIB (free, community-run) or add them manually."}
                  </p>
                )}
                <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  <button className="btn btn-ghost btn-sm" disabled={lyricsBusy} onClick={fetchLyrics}>
                    {lyricsBusy ? "Fetching…" : "Fetch from LRCLIB"}
                  </button>
                  <button className="btn btn-ghost btn-sm" disabled={lyricsBusy}
                    onClick={() => { setLyricsDraft(lyricsQ.data?.lyrics || ""); setLyricsEditing(true); setLyricsMsg(null); }}>
                    {lyricsQ.data?.lyrics ? "Edit" : "Add manually"}
                  </button>
                  {lyricsQ.data?.lyrics ? (
                    <button className="btn btn-danger btn-sm" disabled={lyricsBusy}
                      onClick={() => { if (window.confirm("Remove the lyrics from this track (tag and .lrc sidecar)?")) saveLyrics(""); }}>
                      Remove
                    </button>
                  ) : null}
                  <div style={{ flex: 1 }} />
                  {lyricsMsg && <span style={{ fontSize: 12, color: lyricsMsg.ok ? "var(--ok-fg)" : "var(--err-fg)" }}>{lyricsMsg.text}</span>}
                </div>
              </>
            )}
          </div>
        </div>

        {/* Right column: tap tempo */}
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <div className="card" style={{ position: "sticky", top: 16 }}>
            <div className="section-label">
              <span>Tap tempo</span>
              <span className="section-hint">{tap.taps > 0 ? `${tap.taps} ${tap.taps === 1 ? "tap" : "taps"}` : "press Space"}</span>
            </div>
            <div style={{ position: "relative", marginBottom: 16 }}>
              <button
                ref={tapBtnRef}
                id="tap-btn"
                onClick={() => {
                  spawnRipple();
                  tap.onTap();
                }}
              >
                <div style={{ fontFamily: "var(--mono)", fontSize: 28, fontWeight: 600, letterSpacing: "0.08em", lineHeight: 1 }}>TAP</div>
                <div style={{ fontSize: 10, marginTop: 4, opacity: 0.8, letterSpacing: "0.1em" }}>OR PRESS SPACE</div>
              </button>
            </div>
            <div style={{ textAlign: "center", marginBottom: 14 }}>
              <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--muted)", letterSpacing: "0.1em", textTransform: "uppercase", marginBottom: 4 }}>live estimate</div>
              <div style={{ fontFamily: "var(--mono)", fontSize: 36, fontWeight: 600, color: tap.canApply ? "var(--accent-2)" : "var(--muted)", letterSpacing: "-0.02em", fontVariantNumeric: "tabular-nums", minHeight: 38, lineHeight: 1 }}>
                {tap.display}
              </div>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn btn-ghost btn-sm" style={{ flex: 1 }} onClick={tap.reset}>
                Reset
              </button>
              <button className="btn btn-primary btn-sm" style={{ flex: 1 }} onClick={applyTap} disabled={!tap.canApply}>
                Apply
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M5 12 H19 M13 6 L19 12 L13 18" />
                </svg>
              </button>
            </div>
            <div style={{ marginTop: 12, fontSize: 10, color: "var(--muted)", textAlign: "center", lineHeight: 1.5 }}>Resets after 3s of silence</div>
          </div>
        </div>
      </div>

      {track.artist && <RelatedPanel artist={track.artist} context="track" />}

      {coverPickerOpen && (
        <ImagePicker
          kind="track"
          title={`Cover — ${track.title || fname}`}
          initialQuery={`${track.artist || ""} ${track.title || ""}`.trim() || fname}
          onPick={applyCover}
          onClose={() => setCoverPickerOpen(false)}
        />
      )}

      {detailCand && (() => {
        const c = detailCand;
        // label · candidate value · current form value (to preview what Use changes)
        const rows: [string, string, string][] = [
          ["Title", c.title, metaForm.title],
          ["Artist", c.artist, metaForm.artist],
          ["Album", c.album, metaForm.album],
          ["Album artist", c.album_artist, metaForm.album_artist],
          ["Track #", c.track_no != null ? String(c.track_no) : "", metaForm.track_no],
          ["Disc #", c.disc_no != null ? String(c.disc_no) : "", metaForm.disc_no],
          ["Year", c.year != null ? String(c.year) : "", metaForm.year],
          ["ISRC", c.isrc, metaForm.isrc],
        ];
        return (
          <div
            role="dialog"
            aria-label="Candidate details"
            onClick={() => setDetailCand(null)}
            style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)", zIndex: 200, display: "grid", placeItems: "center", padding: 16 }}
          >
            <div className="card" onClick={(e) => e.stopPropagation()} style={{ width: "min(600px, 100%)", maxHeight: "88vh", overflowY: "auto" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
                <h2 style={{ fontSize: 15, fontWeight: 600, flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {c.title} <span style={{ color: "var(--muted)", fontWeight: 400 }}>— {c.artist}</span>
                </h2>
                <span className="chip chip--neutral" style={{ flexShrink: 0 }}>{c.source}</span>
                <button className="btn btn-ghost btn-sm" onClick={() => setDetailCand(null)} aria-label="Close">✕</button>
              </div>
              <div style={{ display: "flex", gap: 18, flexWrap: "wrap" }}>
                {c.cover_url ? (
                  <img src={c.cover_url} alt="" style={{ width: 210, height: 210, borderRadius: 12, objectFit: "cover", flexShrink: 0, alignSelf: "flex-start" }} />
                ) : (
                  <div className="art-thumb" style={{ width: 210, height: 210, fontSize: 64, flexShrink: 0 }} aria-hidden>♪</div>
                )}
                <div style={{ flex: 1, minWidth: 240, display: "flex", flexDirection: "column", gap: 7, fontSize: 12 }}>
                  {rows.map(([label, cand, cur]) => {
                    const changed = !!cand && cand.trim() !== cur.trim();
                    return (
                      <div key={label} style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
                        <span style={{ width: 86, flexShrink: 0, fontSize: 10, fontFamily: "var(--mono)", textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--muted)" }}>{label}</span>
                        <span style={{ minWidth: 0, wordBreak: "break-word" }}>
                          <span style={{ color: changed ? "var(--accent-2)" : "var(--text)", fontWeight: changed ? 500 : 400 }}>{cand || "—"}</span>
                          {changed && cur.trim() && (
                            <span style={{ color: "var(--muted)", fontSize: 11 }}> (now: {cur})</span>
                          )}
                        </span>
                      </div>
                    );
                  })}
                  <div style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
                    <span style={{ width: 86, flexShrink: 0, fontSize: 10, fontFamily: "var(--mono)", textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--muted)" }}>Duration</span>
                    <span style={{ color: "var(--text)" }}>
                      {fmtDur(c.duration_ms)}
                      <span style={{ color: "var(--muted)", fontSize: 11 }}>
                        {durDelta(c.duration_ms) || ""}{track.duration_ms ? ` (file: ${fmtDur(track.duration_ms)})` : ""}
                      </span>
                    </span>
                  </div>
                  <p style={{ fontSize: 11, color: "var(--muted)", marginTop: 4, lineHeight: 1.5 }}>
                    Highlighted values differ from the current form. Use fills the form only — nothing is written until you Save metadata.
                  </p>
                </div>
              </div>
              <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 16, paddingTop: 12, borderTop: "1px solid var(--border)" }}>
                {c.url && <a href={c.url} target="_blank" rel="noreferrer" style={{ color: "var(--accent-2)", fontSize: 12 }}>Open on {c.source} ↗</a>}
                <div style={{ flex: 1 }} />
                <button className="btn btn-ghost btn-sm" onClick={() => setDetailCand(null)}>Cancel</button>
                <button className="btn btn-primary btn-sm" onClick={() => applyCandidate(c)}>Use these fields</button>
              </div>
            </div>
          </div>
        );
      })()}
    </>
  );
}
