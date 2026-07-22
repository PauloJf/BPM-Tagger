import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { LyricsResponse } from "../lib/types";
import { useAudioTime } from "../hooks/useAudioTime";
import { useResizableDrawer } from "../hooks/useResizableDrawer";
import { MaximizeButton, ResizeHandle } from "./DrawerControls";

export interface LyricLine {
  t: number | null; // seconds, null for plain (untimed) lines
  text: string;
}

// Leading [mm:ss.xx] stamps (a line may carry several, e.g. repeated chorus).
const TS = /\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]/g;

// Lyrics text-size preference, persisted per-browser (mirrors lib/theme.ts).
type LyricsFont = "s" | "m" | "l";
const FONT_KEY = "bpm-lyrics-font";
function initialLyricsFont(): LyricsFont {
  const v = localStorage.getItem(FONT_KEY);
  return v === "s" || v === "l" ? v : "m";
}

/** Parse lyrics text into lines. LRC-timed lines make the result synced
 *  (sorted by time, metadata tags like [ar:…] dropped); otherwise every
 *  non-empty line is kept in order for manual stepping. */
export function parseLyrics(text: string): { lines: LyricLine[]; synced: boolean } {
  const out: LyricLine[] = [];
  let anyTime = false;
  for (const raw of text.split(/\r?\n/)) {
    TS.lastIndex = 0;
    const stamps: number[] = [];
    let lastEnd = 0;
    let m: RegExpExecArray | null;
    while ((m = TS.exec(raw)) && m.index === lastEnd) {
      const frac = m[3] ? parseInt(m[3], 10) / 10 ** m[3].length : 0;
      stamps.push(parseInt(m[1], 10) * 60 + parseInt(m[2], 10) + frac);
      lastEnd = TS.lastIndex;
    }
    const content = raw.slice(lastEnd).trim();
    if (stamps.length) {
      anyTime = true;
      for (const t of stamps) out.push({ t, text: content });
    } else if (!/^\[[a-z]+:.*\]$/i.test(raw.trim())) {
      out.push({ t: null, text: content });
    }
  }
  if (anyTime) {
    const timed = out.filter((l) => l.t != null).sort((a, b) => a.t! - b.t!);
    return { lines: timed, synced: true };
  }
  while (out.length && !out[0].text) out.shift();
  while (out.length && !out[out.length - 1].text) out.pop();
  return { lines: out, synced: false };
}

/** Drawer above the player bar showing the current track's lyrics.
 *  Synced (LRC) lyrics follow the audio clock — the current line is
 *  highlighted and kept centered, and clicking a line seeks to it. Plain
 *  lyrics are stepped manually (click a line or use the ▲/▼ buttons). */
export function LyricsPanel({ path, audioRef, onClose }: {
  path: string;
  audioRef: React.RefObject<HTMLAudioElement>;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const lyricsQ = useQuery({
    queryKey: ["lyrics", path],
    queryFn: () => api.get<LyricsResponse>(`/api/track/lyrics?path=${encodeURIComponent(path)}`),
    enabled: !!path,
  });
  const { time } = useAudioTime(audioRef);
  const parsed = useMemo(
    () => (lyricsQ.data?.lyrics ? parseLyrics(lyricsQ.data.lyrics) : null),
    [lyricsQ.data?.lyrics]
  );

  const [manualIdx, setManualIdx] = useState(0);
  useEffect(() => setManualIdx(0), [path]);

  const bodyRef = useRef<HTMLDivElement>(null);
  // Auto-follow pauses for a few seconds after the user scrolls the panel
  // themselves, so reading ahead isn't fought by the scroller.
  const suspendUntil = useRef(0);

  let activeIdx = -1;
  if (parsed) {
    if (parsed.synced) {
      for (let i = 0; i < parsed.lines.length; i++) {
        if ((parsed.lines[i].t ?? 0) <= time) activeIdx = i;
        else break;
      }
    } else {
      activeIdx = manualIdx;
    }
  }

  useEffect(() => {
    if (!parsed || activeIdx < 0) return;
    if (parsed.synced && Date.now() < suspendUntil.current) return;
    bodyRef.current
      ?.querySelector(`[data-idx="${activeIdx}"]`)
      ?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [activeIdx, parsed]);

  const [fetching, setFetching] = useState(false);
  const [fetchMsg, setFetchMsg] = useState("");
  async function fetchNow() {
    setFetching(true);
    setFetchMsg("");
    try {
      const r = await api.post<{ ok: boolean; error?: string }>(
        "/api/track/lyrics/fetch", { file_path: path });
      if (r.ok) qc.invalidateQueries({ queryKey: ["lyrics", path] });
      else setFetchMsg(r.error || "No lyrics found.");
    } catch (e) {
      setFetchMsg(e instanceof Error ? e.message : "Request failed");
    } finally {
      setFetching(false);
    }
  }

  function clickLine(i: number) {
    if (!parsed) return;
    if (parsed.synced) {
      const t = parsed.lines[i].t;
      if (t != null && audioRef.current) {
        audioRef.current.currentTime = t;
        suspendUntil.current = 0; // resume following right away
      }
    } else {
      setManualIdx(i);
    }
  }

  const step = (d: number) =>
    setManualIdx((i) => Math.max(0, Math.min((parsed?.lines.length ?? 1) - 1, i + d)));

  const drawer = useResizableDrawer({ key: "bpm-lyrics-size" });
  const [font, setFont] = useState<LyricsFont>(initialLyricsFont);
  const changeFont = (f: LyricsFont) => {
    setFont(f);
    try { localStorage.setItem(FONT_KEY, f); } catch { /* private mode */ }
  };

  return (
    <div className={"player-lyrics" + (drawer.maximized ? " maximized" : "")} data-drawer style={drawer.style}>
      {!drawer.small && <ResizeHandle onPointerDown={drawer.startResize} />}
      <div className="player-queue-head">
        <span>
          Lyrics
          {parsed && (
            <span className="chip chip--neutral" style={{ marginLeft: 8, fontSize: 9 }}>
              {parsed.synced ? "synced" : "manual"}
            </span>
          )}
        </span>
        <span style={{ display: "flex", gap: 4, alignItems: "center" }}>
          <span className="lyrics-font-stepper" role="group" aria-label="Lyrics text size">
            {(["s", "m", "l"] as const).map((f) => (
              <button
                key={f}
                className={"btn btn-bare btn-sm" + (font === f ? " active" : "")}
                onClick={() => changeFont(f)}
                aria-pressed={font === f}
                title={f === "s" ? "Small text" : f === "m" ? "Medium text" : "Large text"}
              >
                {f.toUpperCase()}
              </button>
            ))}
          </span>
          <MaximizeButton maximized={drawer.maximized} onToggle={drawer.toggleMaximized} />
          {parsed && !parsed.synced && (
            <>
              <button className="btn btn-bare btn-sm" onClick={() => step(-1)} disabled={activeIdx <= 0}
                aria-label="Previous line" title="Previous line">▲</button>
              <button className="btn btn-bare btn-sm" onClick={() => step(1)}
                disabled={activeIdx >= (parsed.lines.length - 1)}
                aria-label="Next line" title="Next line">▼</button>
            </>
          )}
          <button className="btn btn-bare btn-sm" onClick={onClose} aria-label="Close lyrics">✕</button>
        </span>
      </div>
      <div
        ref={bodyRef}
        className={"player-lyrics-body lyrics-font-" + font}
        onWheel={() => { suspendUntil.current = Date.now() + 4000; }}
        onTouchMove={() => { suspendUntil.current = Date.now() + 4000; }}
      >
        {lyricsQ.isLoading ? (
          <p className="player-lyrics-empty">Loading…</p>
        ) : !parsed || parsed.lines.length === 0 ? (
          <div className="player-lyrics-empty">
            <p style={{ marginBottom: 10 }}>No lyrics on this track.</p>
            <button className="btn btn-ghost btn-sm" disabled={fetching} onClick={fetchNow}>
              {fetching ? "Fetching…" : "Fetch from LRCLIB"}
            </button>
            {fetchMsg && <p style={{ marginTop: 8, color: "var(--err-fg)" }}>{fetchMsg}</p>}
          </div>
        ) : (
          parsed.lines.map((l, i) => (
            <button
              key={i}
              data-idx={i}
              className={"player-lyrics-line" + (i === activeIdx ? " current" : "")}
              onClick={() => clickLine(i)}
              title={parsed.synced ? "Jump to this line" : "Mark as current line"}
            >
              {l.text || " " /* keep blank lines as breathing room */}
            </button>
          ))
        )}
      </div>
    </div>
  );
}
