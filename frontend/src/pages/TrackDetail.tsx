import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, audioUrl } from "../lib/api";
import { basename, dirname } from "../lib/paths";
import type { TrackDetailResponse } from "../lib/types";
import { BpmDisplay } from "../components/BpmDisplay";
import { DetectorBar } from "../components/DetectorBar";
import { useTapTempo } from "../hooks/useTapTempo";
import { useWaveform } from "../hooks/useWaveform";
import { useTitle } from "../hooks/useTitle";

function fmtTime(s: number): string {
  if (!isFinite(s)) return "0:00";
  const m = Math.floor(s / 60);
  return m + ":" + String(Math.floor(s % 60)).padStart(2, "0");
}

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

  const audioRef = useRef<HTMLAudioElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const trackReady = !!detailQ.data?.track;
  const { loading: wfLoading } = useWaveform(canvasRef, audioRef, path, trackReady);
  const tap = useTapTempo();
  const tapBtnRef = useRef<HTMLButtonElement>(null);

  const [playing, setPlaying] = useState(false);
  const [buffering, setBuffering] = useState(false);
  const [curTime, setCurTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const bufWait = useRef<number | undefined>(undefined);

  const [bpmInput, setBpmInput] = useState("");
  const [saveMsg, setSaveMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [unlockMsg, setUnlockMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [reanalyzing, setReanalyzing] = useState(false);
  const [reanalyzeMsg, setReanalyzeMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [metaForm, setMetaForm] = useState({ title: "", artist: "", album: "", album_artist: "", track_no: "", disc_no: "", year: "", isrc: "" });
  const [applyTemplate, setApplyTemplate] = useState(false);
  const [metaMsg, setMetaMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const detail = detailQ.data;
  const track = detail?.track;
  const playbackBuffer = detail?.playback_buffer ?? 3;

  // Seed the BPM override input + metadata form when the track loads/changes.
  useEffect(() => {
    setBpmInput(track?.bpm ? track.bpm.toFixed(1) : "");
    setSaveMsg(null);
    setUnlockMsg(null);
    setReanalyzeMsg(null);
    setMetaMsg(null);
    setPlaying(false);
    setBuffering(false);
    if (track) {
      const s = (v: unknown) => (v == null ? "" : String(v));
      setMetaForm({ title: s(track.title), artist: s(track.artist), album: s(track.album),
        album_artist: s(track.album_artist), track_no: s(track.track_no), disc_no: s(track.disc_no),
        year: s(track.year), isrc: s(track.isrc) });
    }
  }, [track?.file_path, track?.bpm]);

  async function saveMeta() {
    setMetaMsg(null);
    try {
      const res = await api.put<{ ok: boolean; file_path?: string; error?: string }>(
        "/api/track/tags", { file_path: path, ...metaForm, apply_template: applyTemplate });
      if (!res.ok) { setMetaMsg({ ok: false, text: res.error || "Failed" }); return; }
      qc.invalidateQueries({ queryKey: ["tracks"] });
      if (res.file_path && res.file_path !== path) {
        navigate(`/track?path=${encodeURIComponent(res.file_path)}&back=${params.get("back") || "tracks"}`, { replace: true });
      } else {
        qc.invalidateQueries({ queryKey: ["track", path] });
        setMetaMsg({ ok: true, text: "Saved" });
      }
    } catch (e) {
      setMetaMsg({ ok: false, text: e instanceof Error ? e.message : "Request failed" });
    }
  }

  // Audio element listeners for the time display.
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    const onTime = () => setCurTime(audio.currentTime);
    const onMeta = () => setDuration(audio.duration || 0);
    const onEnded = () => setPlaying(false);
    audio.addEventListener("timeupdate", onTime);
    audio.addEventListener("loadedmetadata", onMeta);
    audio.addEventListener("ended", onEnded);
    return () => {
      audio.removeEventListener("timeupdate", onTime);
      audio.removeEventListener("loadedmetadata", onMeta);
      audio.removeEventListener("ended", onEnded);
    };
  }, [path]);

  function bufferedEnd(audio: HTMLAudioElement) {
    for (let i = 0; i < audio.buffered.length; i++) {
      if (audio.buffered.start(i) <= audio.currentTime + 0.1 && audio.buffered.end(i) >= audio.currentTime)
        return audio.buffered.end(i);
    }
    return 0;
  }
  function enoughBuffered(audio: HTMLAudioElement) {
    const target = audio.currentTime + playbackBuffer;
    return bufferedEnd(audio) >= Math.min(target, audio.duration || target);
  }
  function doPlay(audio: HTMLAudioElement) {
    audio.play();
    setBuffering(false);
    setPlaying(true);
  }
  function togglePlay() {
    const audio = audioRef.current;
    if (!audio) return;
    if (!audio.paused || bufWait.current) {
      if (bufWait.current) {
        window.clearInterval(bufWait.current);
        bufWait.current = undefined;
        setBuffering(false);
      }
      audio.pause();
      setPlaying(false);
      return;
    }
    if (playbackBuffer <= 0 || enoughBuffered(audio)) {
      doPlay(audio);
    } else {
      setBuffering(true);
      bufWait.current = window.setInterval(() => {
        if (enoughBuffered(audio)) {
          window.clearInterval(bufWait.current);
          bufWait.current = undefined;
          doPlay(audio);
        }
      }, 150);
    }
  }
  useEffect(() => () => {
    if (bufWait.current) window.clearInterval(bufWait.current);
  }, []);

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

  const back = detail!.back;
  const backUrl =
    back === "review"
      ? "/review"
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
      <div style={{ marginBottom: 22 }}>
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
          <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)", marginLeft: "auto" }}>
            {track.analyzed_at ? track.analyzed_at.slice(0, 16).replace("T", " ") + " UTC" : ""}
          </span>
        </div>
        <h1 style={{ fontSize: 22, fontWeight: 600, letterSpacing: "-0.015em", marginBottom: 4, wordBreak: "break-word" }}>{fname}</h1>
        <div style={{ fontSize: 12, color: "var(--muted)", fontFamily: "var(--mono)", display: "flex", alignItems: "center", gap: 6 }}>
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round">
            <path d="M3 7 a2 2 0 0 1 2 -2 h4 l2 2 h8 a2 2 0 0 1 2 2 v9 a2 2 0 0 1 -2 2 H5 a2 2 0 0 1 -2 -2 Z" />
          </svg>
          {fdir}
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
            <audio ref={audioRef} preload="auto" src={audioUrl(track.file_path)} style={{ display: "none" }} />
            <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
              <button
                onClick={togglePlay}
                style={{ width: 44, height: 44, borderRadius: 999, background: "var(--accent)", color: "white", border: "none", cursor: "pointer", display: "grid", placeItems: "center", flexShrink: 0, boxShadow: "0 4px 16px var(--accent-glow)", opacity: buffering ? 0.55 : 1 }}
              >
                {buffering ? (
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                    <circle cx="12" cy="12" r="8" stroke="white" strokeWidth="2.5" fill="none" strokeDasharray="22 10" strokeLinecap="round" style={{ transformOrigin: "12px 12px", animation: "spin 0.8s linear infinite" }} />
                  </svg>
                ) : playing ? (
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
                <span style={{ color: "var(--text)" }}>{fmtTime(curTime)}</span>
                <span> / {fmtTime(duration)}</span>
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
    </>
  );
}
