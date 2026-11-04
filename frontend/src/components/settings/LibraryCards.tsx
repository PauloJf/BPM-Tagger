import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { Toggle } from "../Toggle";
import { SaveButton, fieldLabel, readers, useSaveSection, useSettings, type Saved } from "./shared";

/** Settings → Library & scanning tab: public URL and paths, scan behaviour, mode, ISRC fill. */

const MODES = ["watch", "watch_all", "scan_unscanned", "scan_all", "scan_review", "report"];

interface IsrcCand { source: string; isrc: string; title: string; artist: string; url: string }
interface FillStatus {
  running: boolean; total: number; done: number; filled: number;
  unresolved: { file_path: string; title: string; artist: string; candidates: IsrcCand[] }[];
}
interface WaveformFillStatus {
  running: boolean; total: number; done: number; filled: number; failed: number; remaining: number;
}

export function LibraryCard() {
  const { cfg } = useSettings();
  const saveSection = useSaveSection();
  const [library, setLibrary] = useState({ publicUrl: "", pathTemplate: "" });
  const [librarySaved, setLibrarySaved] = useState<Saved>("");
  useEffect(() => {
    if (!cfg) return;
    const { s } = readers(cfg);
    setLibrary({ publicUrl: s("ui_public_url"), pathTemplate: s("path_template") });
  }, [cfg]);

  function previewPath(tpl: string): string {
    const sample: Record<string, string | number> = {
      AlbumArtist: "The Weeknd", Artist: "The Weeknd", Album: "After Hours",
      Title: "Blinding Lights", TrackNo: 3, DiscNo: 1, Year: 2020,
      ext: "mp3",
    };
    return tpl.replace(/\{(\w+)(?::0(\d)d)?\}/g, (_m, k, pad) => {
      let v = sample[k] ?? "";
      if (pad && typeof v === "number") v = String(v).padStart(+pad, "0");
      return String(v);
    });
  }

  return (
    <>
      {/* Library */}
      <div id="sec-library" className="settings-card card">
        <div className="settings-card-header">
          <h2>Library</h2>
          <p>Where this UI is reachable, and how files are named when you edit a track's tags.</p>
        </div>
        <div className="settings-fields">
          <div className="field-row">
            {fieldLabel("Public URL", "Public address of this UI. Marks the session cookie Secure over https and builds the links in ntfy notifications (e.g. https://bpm.example.com)")}
            <input type="text" value={library.publicUrl}
                   onChange={(e) => setLibrary({ ...library, publicUrl: e.target.value })}
                   placeholder="https://bpm.example.com"
                   style={{ maxWidth: 340, width: "100%", fontFamily: "var(--mono)", fontSize: 12 }} />
          </div>
          <div className="field-row">
            {fieldLabel("Path template", "File layout used when editing a track's tags renames the file")}
            <div style={{ width: "100%", maxWidth: 360 }}>
              <input type="text" value={library.pathTemplate} placeholder="{AlbumArtist}/{Album}/{TrackNo:02d} - {Title}.{ext}"
                     onChange={(e) => setLibrary({ ...library, pathTemplate: e.target.value })}
                     style={{ width: "100%", fontFamily: "var(--mono)", fontSize: 12 }} />
              <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
                → {previewPath(library.pathTemplate || "{AlbumArtist}/{Album}/{TrackNo:02d} - {Title}.{ext}")}
              </div>
            </div>
          </div>
          <div>
            <button
              type="button"
              className={"btn btn-sm " + (librarySaved === "ok" ? "btn-ghost" : "btn-primary")}
              disabled={librarySaved === "saving"}
              onClick={() => saveSection("/api/settings/library", {
                ui_public_url: library.publicUrl,
                path_template: library.pathTemplate,
              }, setLibrarySaved)}
            >
              {librarySaved === "saving" ? "Saving…" : librarySaved === "ok" ? "Saved ✓" : "Save Library Settings"}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

export function ScanCard() {
  const qc = useQueryClient();
  const { cfg, isLocked } = useSettings();
  const saveSection = useSaveSection();
  const [scan, setScan] = useState({ workers: 1, bpmMin: 60, bpmMax: 200, useDr: true, useEs: true, writeTags: true, preserveMtime: true, waveforms: "auto", conf: 0.4 });
  const [scanSaved, setScanSaved] = useState<Saved>("");
  const [hashMsg, setHashMsg] = useState("");
  const [reindexMsg, setReindexMsg] = useState("");
  useEffect(() => {
    if (!cfg) return;
    const { s, n, b } = readers(cfg);
    setScan({ workers: n("workers", 1), bpmMin: Math.round(n("bpm_min", 60)), bpmMax: Math.round(n("bpm_max", 200)), useDr: b("use_deeprhythm", true), useEs: b("use_essentia", true), writeTags: b("write_tags", true), preserveMtime: b("preserve_mtime", true), waveforms: s("compute_waveforms", "auto") || "auto", conf: n("review_confidence_threshold", 0.4) });
  }, [cfg]);

  // Waveform back-fill (tracks scanned while COMPUTE_WAVEFORMS skipped them).
  const waveformFillQ = useQuery({
    queryKey: ["waveform-fill"],
    queryFn: () => api.get<WaveformFillStatus>("/api/waveform/fill/status"),
    refetchInterval: (q) => (q.state.data?.running ? 1500 : false),
  });
  async function startWaveformFill() {
    await api.post("/api/waveform/fill/start", {});
    qc.invalidateQueries({ queryKey: ["waveform-fill"] });
  }
  async function cancelWaveformFill() {
    await api.post("/api/waveform/fill/cancel", {});
    qc.invalidateQueries({ queryKey: ["waveform-fill"] });
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

  return (
    <>
      {/* Scan Behavior */}
      <div id="sec-scan" className="settings-card card">
        <div className="settings-card-header">
          <h2>Scan Behavior</h2>
          <p>Tune the BPM detector stack.</p>
        </div>
        <form onSubmit={(e) => { e.preventDefault(); saveSection("/api/settings/scan", { workers: scan.workers, bpm_min: scan.bpmMin, bpm_max: scan.bpmMax, use_deeprhythm: scan.useDr, use_essentia: scan.useEs, write_tags: scan.writeTags, preserve_mtime: scan.preserveMtime, compute_waveforms: scan.waveforms, review_confidence_threshold: scan.conf }, setScanSaved); }}>
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
              {fieldLabel("Waveforms", "Peaks for the player and track pages — a second decode per track. Auto computes them only while the web UI is on")}
              <select value={scan.waveforms} onChange={(e) => setScan({ ...scan, waveforms: e.target.value })} style={{ fontFamily: "var(--mono)", fontSize: 12 }}>
                <option value="auto">Auto (with UI)</option>
                <option value="true">Always</option>
                <option value="false">Never</option>
              </select>
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
        {(() => {
          const f = waveformFillQ.data;
          // Nothing to offer until a headless/"Never" scan has left gaps.
          if (!f || (!f.running && f.total === 0 && f.remaining === 0)) return null;
          return (
            <div style={{ marginTop: 18, paddingTop: 14, borderTop: "1px solid var(--border)" }}>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>Fill missing waveforms</div>
              <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 10, maxWidth: 480, lineHeight: 1.5 }}>
                Some tracks were scanned without waveforms. They're computed on demand when played; this fills them all now, one file at a time.
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                <button className="btn btn-primary btn-sm" type="button"
                        disabled={f.running || f.remaining === 0} onClick={startWaveformFill}>
                  {f.running ? "Computing…" : "Fill missing waveforms"}
                </button>
                {f.running && (
                  <button className="btn btn-ghost btn-sm" type="button" onClick={cancelWaveformFill}>Cancel</button>
                )}
                {(f.running || f.total > 0) && (
                  <span style={{ fontSize: 12, color: "var(--muted)", fontFamily: "var(--mono)" }}>
                    {f.done}/{f.total} done · {f.filled} filled · {f.failed} failed
                  </span>
                )}
                {!f.running && (
                  <span style={{ fontSize: 12, color: "var(--muted)" }}>
                    {f.remaining === 0 ? "Every track has a waveform." : `${f.remaining} track${f.remaining === 1 ? "" : "s"} without one.`}
                  </span>
                )}
              </div>
            </div>
          );
        })()}
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
    </>
  );
}

export function ModeCard() {
  const { cfg } = useSettings();
  const saveSection = useSaveSection();
  const [mode, setMode] = useState("watch");
  const [modeSaved, setModeSaved] = useState<Saved>("");
  useEffect(() => {
    if (!cfg) return;
    const { s } = readers(cfg);
    setMode(s("mode", "watch") || "watch");
  }, [cfg]);

  return (
    <>
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
    </>
  );
}

export function IsrcCard() {
  const qc = useQueryClient();
  // Bulk ISRC fill: poll status while the job runs; hide rows the user resolves.
  const fillQ = useQuery({
    queryKey: ["isrc-fill"],
    queryFn: () => api.get<FillStatus>("/api/isrc/fill/status"),
    refetchInterval: (q) => (q.state.data?.running ? 1500 : false),
  });
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

  return (
    <>
      {/* ISRC bulk fill */}
      <div id="sec-isrc" className="settings-card card">
        <div className="settings-card-header">
          <h2>Fill missing ISRCs</h2>
          <p>Look up the ISRC for every library track that's missing one (Deezer / MusicBrainz) and write it to the tag. A confident, duration-matched single result is filled automatically; anything uncertain or not found is listed below for you to choose.</p>
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
    </>
  );
}
