import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { usePlayer, type ListenSource, type PlayerTrack } from "../lib/player";
import type { ListenQueueResponse, RunPlaylistOption, TrackDetailResponse } from "../lib/types";
import { fmtTime, useAudioTime } from "../hooks/useAudioTime";
import { useWaveform } from "../hooks/useWaveform";
import { useTitle } from "../hooks/useTitle";
import { useCoverGlow } from "../hooks/useCoverGlow";
import { useIsMobile } from "../hooks/useIsMobile";
import { ArtistLinks } from "../components/ArtistLinks";
import { LyricsPanel } from "../components/LyricsPanel";
import { BpmDisplay } from "../components/BpmDisplay";
import PlayerCover from "../components/PlayerCover";
import QueueList from "../components/QueueList";
import PageHeader from "../components/PageHeader";

const SOURCE_KEY = "bpm.listen.source";   // "library" | "mine" | "pl:<id>"

/**
 * Listen — the regular (non-cadence) player. A thin view over the shared player
 * engine: pick a source (the whole library, a playlist, or a scoped player's
 * pooled "All my music"), play it in order or shuffled, and optionally keep it
 * going with radio mode (auto-refill from the same source). Serves two
 * audiences with the same page: admin/guest get a proper now-playing screen for
 * whatever queue they built anywhere in the app; the kiosk (player role) gets
 * its first way to play music without a tempo lock — gated by the admin's
 * player_listen_mode setting (see App.tsx's PlayerLayout).
 *
 * Desktop (≥900px) uses Run's two-column shell (.run-desktop-body): the player
 * owns the left column — a height-aware cover with the title centered in the
 * leftover space, transport and mode row at the bottom — and the queue lives
 * permanently in a full-height panel on the right, ending level with the
 * transport. The source picker rides in the queue panel (as on mobile's Queue
 * tab), so switching sources never covers the artwork; pre-playback it shows
 * inline with the empty state instead.
 *
 * Mobile (<900px) reuses Run's one-screen machinery (.run-mobile-fill): the
 * cover absorbs the leftover height, the transport pins low, and a bottom
 * Playing/Queue switcher — bottom rather than Run's top tabs, both for thumb
 * reach and so the two players read differently at a glance — swaps the cover
 * for the queue (where the source picker lives mid-playback, as on Run).
 */
export default function Listen() {
  useTitle("Listen");
  const { role, fullAccess, listenMode } = useAuth();
  const playerMode = role === "player";
  const player = usePlayer();
  const { current, playing, audioRef, radio, setRadio, listenSource, tempoLock } = player;
  const { time, dur } = useAudioTime(audioRef);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const isPreview = !!current?.ephemeral;
  useWaveform(canvasRef, audioRef, current?.path || "", !!current && !isPreview, !!current && !isPreview);
  const glow = useCoverGlow(current && !isPreview ? current.path : null);
  const mobile = useIsMobile(900);
  const [view, setView] = useState<"playing" | "queue">("playing");
  const [lyricsOpen, setLyricsOpen] = useState(false);
  const [starting, setStarting] = useState(false);
  const [startErr, setStartErr] = useState("");
  const [dislikedPaths, setDislikedPaths] = useState<Set<string>>(() => new Set());

  // Playlist sources — the same session-scoped list the Run page draws from
  // (admin/guest: every playlist; a named player: its associated ones).
  const playlistsQ = useQuery({
    queryKey: ["run-playlists"],
    queryFn: () => api.get<{ playlists: RunPlaylistOption[] }>("/api/run/playlists"),
    staleTime: 30_000,
  });
  const playlists = playlistsQ.data?.playlists ?? [];
  const [source, setSourceState] = useState<string>(() => localStorage.getItem(SOURCE_KEY) || "");
  const setSource = (s: string) => { setSourceState(s); localStorage.setItem(SOURCE_KEY, s); };
  const library = source === "library";
  const pooled = source === "mine";
  const selectedPlaylistId = source.startsWith("pl:") ? Number(source.slice(3)) : null;
  const selectedPlaylist = playlists.find((p) => p.id === selectedPlaylistId) ?? null;
  // No remembered source (or a stale one): full-access sessions default to the
  // whole library; a scoped player to its pooled source (several playlists) or
  // its only playlist. Mirrors the Run picker's access rules.
  useEffect(() => {
    if (!playlistsQ.data) return;
    const valid = library ? fullAccess
      : pooled ? !fullAccess && playlists.length > 1
      : !!selectedPlaylist;
    if (valid) return;
    if (fullAccess) setSource("library");
    else if (playlists.length > 1) setSource("mine");
    else if (playlists.length > 0) setSource(`pl:${playlists[0].id}`);
  }, [playlistsQ.data, library, pooled, selectedPlaylist, fullAccess, playlists]);

  // Current track detail (fresh BPM + star state) — same cache key the other
  // players use, so a fix made on the track page is reflected here immediately.
  const trackQ = useQuery({
    queryKey: ["track-bpm", current?.path || ""],
    queryFn: () => api.get<TrackDetailResponse>(`/api/track?path=${encodeURIComponent(current!.path)}`),
    enabled: !!current && !isPreview,
    staleTime: 60_000,
  });
  const detail = trackQ.data?.track;
  const bpm = current?.bpm ?? detail?.bpm ?? null;
  const starred = current?.starred ?? Boolean(detail?.starred);
  const currentDisliked = !!current && dislikedPaths.has(current.path);

  async function startPlayback(shuffle: boolean) {
    if (!source) return;
    setStarting(true);
    setStartErr("");
    try {
      const src = library ? "library" : pooled ? "mine" : String(selectedPlaylistId);
      const resp = await api.get<ListenQueueResponse>(`/api/listen/queue?playlist=${src}`);
      if (!resp.tracks.length) {
        setStartErr(selectedPlaylist
          ? `No playable tracks in “${selectedPlaylist.name}” — none of its entries matched a local file.`
          : library
          ? "No playable tracks in your library yet."
          : "No playable tracks in your playlists yet.");
        return;
      }
      const tracks: PlayerTrack[] = resp.tracks.map((t) => ({
        path: t.path, title: t.title, artist: t.artist, bpm: t.bpm,
        starred: t.starred, loudnessLufs: t.loudness_lufs,
      }));
      const scope: ListenSource = library ? "library" : pooled ? "mine" : selectedPlaylistId;
      // ⚠ ORDER IS LOAD-BEARING (mirrors Run's startRun): playQueue() clears the
      // tempo lock and both source scopes so a new queue never inherits a stale
      // run/radio refill — so the Listen source must be re-set *after* it.
      player.playQueue(tracks, 0, { shuffle });
      player.setListenSource(scope);
      setView("playing");
    } catch (e) {
      setStartErr(e instanceof Error ? e.message : "Failed to load the source");
    } finally {
      setStarting(false);
    }
  }

  function toggleStar() {
    if (!current) return;
    const next = !starred;
    player.setTrackStarred(current.path, next);
    api.post("/api/track/star", { path: current.path, starred: next }).catch(() => {});
  }

  function toggleDislike() {
    if (!current) return;
    const next = !currentDisliked;
    setDislikedPaths((s) => {
      const n = new Set(s);
      if (next) n.add(current.path); else n.delete(current.path);
      return n;
    });
    api.post("/api/track/dislike", { path: current.path, disliked: next }).catch(() => {});
    if (next) player.next();
  }

  const ctlBtn: React.CSSProperties = {
    width: 52, height: 52, borderRadius: 999, display: "inline-flex", alignItems: "center",
    justifyContent: "center", background: "var(--surface)", border: "1px solid var(--border)",
    color: "var(--text)", cursor: "pointer",
  };
  const smallCtl = (active: boolean): React.CSSProperties => ({
    ...ctlBtn, width: 40, height: 40,
    color: active ? "var(--accent-2)" : "var(--muted)",
    borderColor: active ? "var(--accent-border)" : "var(--border)",
    background: active ? "var(--accent-soft)" : "var(--surface)",
  });

  // Ambient glow tinted from the cover, exactly as on Run (portaled to <body>
  // because .container briefly carries a transform during its page-enter
  // animation, which would re-anchor a fixed-position child).
  const glowLayer = createPortal(
    <div aria-hidden style={{ position: "fixed", inset: 0, zIndex: -1, pointerEvents: "none", transition: "opacity 0.45s ease", background: glow.background, opacity: glow.opacity }} />,
    document.body,
  );

  // One status note at a time, highest priority first (matches Run's ordering).
  const statusNote: { color: string; body: React.ReactNode } | null =
    !player.online
      ? { color: "var(--warn-fg)", body: <><span className="conn-dot conn-dot--off" /> Offline — waiting for connection…</> }
      : player.error
      ? { color: "var(--err-fg)", body: player.error }
      : player.buffering
      ? { color: "var(--warn-fg)", body: <>Buffering{player.bufferedPct > 0 ? ` · ${player.bufferedPct}%` : "…"}</> }
      : startErr
      ? { color: "var(--err-fg)", body: startErr }
      : null;

  // Source picker + Play/Shuffle. "Whole library" for full-access sessions only
  // (admin + the shared Guest); "All my music" pools a scoped player's
  // playlists once it has two or more — the same access rules as Run's picker.
  const hasSources = fullAccess || playlists.length > 0;
  const sourcePicker = hasSources && (
    <div style={{ display: "flex", alignItems: "center", gap: 8, justifyContent: "center", flexWrap: "wrap" }}>
      <select
        value={source}
        onChange={(e) => setSource(e.target.value)}
        aria-label="Source to play"
        style={{ fontSize: 13, padding: "7px 10px", borderRadius: 10, background: "var(--surface)", border: "1px solid var(--border)", color: "var(--text)", maxWidth: 240 }}
      >
        {fullAccess && <option value="library">Whole library</option>}
        {!fullAccess && playlists.length > 1 && <option value="mine">All my music</option>}
        {playlists.map((p) => (
          <option key={p.id} value={`pl:${p.id}`}>{p.name} ({p.total})</option>
        ))}
      </select>
      <button className="btn btn-primary" style={{ minHeight: 36 }} disabled={starting || !source} onClick={() => startPlayback(false)}>
        {starting ? "Loading…" : "Play"}
      </button>
      <button
        className="btn btn-ghost" style={{ minHeight: 36 }} disabled={starting || !source}
        onClick={() => startPlayback(true)}
        title="Play this source in random order"
      >
        Shuffle
      </button>
    </div>
  );

  const tempoLockChip = tempoLock && (
    <div style={{ display: "flex", justifyContent: "center", marginTop: 8 }}>
      {playerMode && listenMode === "only" ? (
        <span style={{ fontSize: 12, color: "var(--warn-fg)" }}>
          ⚠ Tempo locked to {tempoLock.target} BPM — tracks play stretched
        </span>
      ) : (
        <Link to="/run" style={{ fontSize: 12, color: "var(--warn-fg)", textDecoration: "none" }} title="Open Run mode">
          ⚠ Tempo locked to {tempoLock.target} BPM — open Run to manage the lock
        </Link>
      )}
    </div>
  );

  const titleBlock = current && (
    <div style={{ textAlign: "center" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 10, minWidth: 0 }}>
        {playerMode || isPreview ? (
          <span style={{ fontSize: 18, fontWeight: 600, letterSpacing: "-0.015em", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{current.title}</span>
        ) : (
          <Link
            to={`/track?path=${encodeURIComponent(current.path)}`}
            title="Open the track page"
            style={{ fontSize: 18, fontWeight: 600, letterSpacing: "-0.015em", color: "inherit", textDecoration: "none", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
          >
            {current.title}
          </Link>
        )}
        {bpm != null && (
          <span style={{ flexShrink: 0 }} title={`${Math.round(bpm)} BPM — the dot pulses on the beat`}>
            <BpmDisplay bpm={bpm} sizePx={15} dotPx={8} pulsing={playing && !tempoLock} beatMs={Math.round(60000 / bpm)} />
          </span>
        )}
      </div>
      {current.artist && (
        playerMode || isPreview ? (
          <div style={{ fontSize: 13, color: "var(--muted)", marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{current.artist}</div>
        ) : (
          <div style={{ marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            <ArtistLinks
              artist={current.artist}
              style={{ fontSize: 13, color: "var(--muted)" }}
              linkStyle={{ color: "inherit", textDecoration: "none" }}
              title={`View ${current.artist}`}
            />
          </div>
        )
      )}
      {tempoLockChip}
    </div>
  );

  // Waveform seek + times + main buttons. Rendered in both mobile views (pause/
  // skip stays reachable while browsing the queue), pinned above the bottom tabs.
  const transportBlock = current && (
    <div style={{ marginTop: mobile ? 8 : 14 }}>
      <div style={{ position: "relative", height: 0 }}>
        {statusNote && (
          <div style={{ position: "absolute", bottom: 6, left: 0, right: 0, display: "flex", justifyContent: "center", zIndex: 5, pointerEvents: "none" }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: statusNote.color, padding: "5px 12px", borderRadius: 12, background: "var(--surface)", border: "1px solid var(--border)" }}>
              {statusNote.body}
            </span>
          </div>
        )}
      </div>
      <canvas ref={canvasRef} style={{ width: "100%", height: 44, display: "block", cursor: "pointer" }} />
      <div style={{ display: "flex", justifyContent: "space-between", fontFamily: "var(--mono)", fontSize: 12, color: "var(--muted)", marginTop: 4 }}>
        <span>{fmtTime(time)}</span>
        <span>-{fmtTime(Math.max(0, dur - time))}</span>
      </div>
      <div style={{ display: "flex", justifyContent: "center", alignItems: "center", gap: 18, marginTop: 10 }}>
        <button
          style={{ ...ctlBtn, width: 40, height: 40, color: starred ? "var(--warn-fg)" : "var(--muted)", borderColor: starred ? "var(--warn-fg)" : "var(--border)" }}
          onClick={toggleStar}
          aria-pressed={starred}
          aria-label={starred ? "Unstar" : "Star"}
          title={starred ? "Unstar" : "Star this track"}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill={starred ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round">
            <polygon points="12,2.5 15,9 22,9.8 17,14.6 18.2,21.6 12,18.2 5.8,21.6 7,14.6 2,9.8 9,9" />
          </svg>
        </button>
        <button style={ctlBtn} onClick={player.prev} aria-label="Previous" title="Previous">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M6 5v14h2V5H6zm3 7l11 7V5l-11 7z" /></svg>
        </button>
        <button
          style={{ ...ctlBtn, width: mobile ? 64 : 72, height: mobile ? 64 : 72, background: "var(--accent)", border: "none", boxShadow: "0 12px 40px -10px var(--accent)" }}
          onClick={player.toggle}
          aria-label={playing ? "Pause" : "Play"}
        >
          {playing ? (
            <svg width="26" height="26" viewBox="0 0 24 24" fill="white"><rect x="6" y="4" width="4" height="16" rx="1" /><rect x="14" y="4" width="4" height="16" rx="1" /></svg>
          ) : (
            <svg width="26" height="26" viewBox="0 0 24 24" fill="white" style={{ marginLeft: 3 }}><polygon points="6,4 20,12 6,20" /></svg>
          )}
        </button>
        <button style={ctlBtn} onClick={player.next} aria-label="Next" title="Next">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M16 5v14h2V5h-2zM4 19l11-7L4 5v14z" /></svg>
        </button>
        <button
          style={{ ...ctlBtn, width: 40, height: 40, color: currentDisliked ? "var(--err-fg)" : "var(--muted)", borderColor: currentDisliked ? "var(--err-fg)" : "var(--border)" }}
          onClick={toggleDislike}
          aria-pressed={currentDisliked}
          aria-label={currentDisliked ? "Remove dislike" : "Dislike"}
          title={currentDisliked ? "Remove dislike" : "Dislike — skips now and is never auto-picked again"}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill={currentDisliked ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17" />
          </svg>
        </button>
      </div>
    </div>
  );

  // Shuffle / repeat / radio / lyrics (+ volume on desktop — phones have
  // hardware volume and the mobile column can't spare the row).
  const modeRow = current && (
    <div style={{ display: "flex", justifyContent: "center", alignItems: "center", gap: 12, marginTop: mobile ? 10 : 14, flexWrap: "wrap" }}>
      <button
        style={smallCtl(player.shuffle)}
        onClick={player.toggleShuffle}
        aria-pressed={player.shuffle}
        aria-label="Shuffle"
        title={player.shuffle ? "Shuffle: on" : "Shuffle: off"}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M16 3h5v5M4 20L21 3M21 16v5h-5M15 15l6 6M4 4l5 5" />
        </svg>
      </button>
      <button
        style={smallCtl(player.repeat !== "off")}
        onClick={player.cycleRepeat}
        aria-label={`Repeat: ${player.repeat}`}
        title={`Repeat: ${player.repeat}`}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M17 1l4 4-4 4" /><path d="M3 11V9a4 4 0 0 1 4-4h14" /><path d="M7 23l-4-4 4-4" /><path d="M21 13v2a4 4 0 0 1-4 4H3" />
        </svg>
        {player.repeat === "one" && <span style={{ fontFamily: "var(--mono)", fontSize: 9, marginLeft: 1 }}>1</span>}
      </button>
      <button
        data-testid="radio-toggle"
        style={{ ...smallCtl(radio), width: "auto", padding: "0 14px", gap: 6, opacity: listenSource == null && !radio ? 0.55 : 1 }}
        onClick={() => setRadio(!radio)}
        aria-pressed={radio}
        aria-label="Radio — keep playing from this source"
        title={listenSource == null
          ? "Radio — start a source from the picker first, then this keeps it playing past the end"
          : radio ? "Radio on — the queue refills from this source as it runs out" : "Radio — keep the queue refilling from this source"}
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="2" />
          <path d="M16.24 7.76a6 6 0 0 1 0 8.49M7.76 16.24a6 6 0 0 1 0-8.49M19.07 4.93a10 10 0 0 1 0 14.14M4.93 19.07a10 10 0 0 1 0-14.14" />
        </svg>
        <span style={{ fontSize: 12, fontWeight: 600 }}>Radio</span>
      </button>
      {!playerMode && !isPreview && (
        <button
          style={smallCtl(lyricsOpen)}
          onClick={() => setLyricsOpen((o) => !o)}
          aria-label="Show lyrics"
          aria-expanded={lyricsOpen}
          title="Lyrics"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
            <path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v3" />
          </svg>
        </button>
      )}
      {!mobile && (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }} title={`Volume ${Math.round(player.volume * 100)}%`}>
          <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" style={{ color: "var(--muted)", flexShrink: 0 }}>
            <path d="M3 9v6h4l5 5V4L7 9H3z" />{player.volume > 0.05 && <path d="M16 8a5 5 0 0 1 0 8" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />}
          </svg>
          <input type="range" min={0} max={1} step={0.01} value={player.volume}
                 onChange={(e) => player.setVolume(+e.target.value)} aria-label="Volume" style={{ width: 90 }} />
        </span>
      )}
    </div>
  );

  const emptyState = !current && (
    <div style={{ textAlign: "center", padding: "24px 0 10px", color: "var(--muted)" }}>
      <div style={{ fontSize: 40, marginBottom: 10 }} aria-hidden>♪</div>
      {hasSources ? (
        <p style={{ fontSize: 13, margin: 0 }}>Pick a source above and hit Play.</p>
      ) : playlistsQ.isLoading ? (
        <p style={{ fontSize: 13, margin: 0 }}>Loading playlists…</p>
      ) : (
        <p style={{ fontSize: 13, margin: 0 }}>No playlists have been shared with this account yet.</p>
      )}
      {!playerMode && (
        <p style={{ fontSize: 12, margin: "8px 0 0" }}>
          You can also start playback from any album, artist, playlist or track page — it shows up here.
        </p>
      )}
      {statusNote && (
        <div style={{ marginTop: 12, fontSize: 12, color: statusNote.color }}>{statusNote.body}</div>
      )}
    </div>
  );

  // The queue card. Both hosts feed it a bounded box and the list scrolls
  // internally: "fill" (mobile Queue tab) shrinks to the fixed column's
  // leftover height; "column" (desktop) absolutely fills the side column —
  // like Run's, so the queue never drives the row height and its bottom always
  // lines up with the transport. The source picker lives in the panel in both,
  // reachable mid-playback without covering the artwork.
  const queuePanel = (variant: "fill" | "column") => (
    <div className="card" style={variant === "fill"
      ? { padding: 0, display: "flex", flexDirection: "column", minHeight: 0, flexShrink: 1, marginBottom: 10 }
      : { padding: 0, margin: 0, position: "absolute", inset: 0, display: "flex", flexDirection: "column" }}>
      <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "baseline", gap: 8, flexShrink: 0 }}>
        <span style={{ fontWeight: 600, fontSize: 13 }}>Queue · {player.orderedQueue.length}</span>
        {radio && listenSource != null && (
          <span style={{ fontSize: 11, color: "var(--muted)" }}>radio keeps this topped up</span>
        )}
        <button className="btn btn-bare btn-sm" style={{ marginLeft: "auto" }} onClick={player.stop} title="Stop and clear the queue">Clear</button>
      </div>
      {sourcePicker && (
        <div data-testid="queue-source" style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
          {sourcePicker}
        </div>
      )}
      <div style={variant === "column"
        ? { flex: 1, minHeight: 0, overflowY: "auto" }
        : { minHeight: 0, overflowY: "auto" }}>
        <QueueList />
      </div>
    </div>
  );

  const lyricsDrawer = lyricsOpen && current && (
    <div style={{ position: "fixed", left: 0, right: 0, bottom: 0, zIndex: 61 }}>
      <LyricsPanel path={current.path} audioRef={audioRef} onClose={() => setLyricsOpen(false)} />
    </div>
  );

  if (!mobile) {
    // Desktop cover: height-aware, like Run's — the viewport height minus the
    // column's fixed chrome (page header + title + transport + mode row), so it
    // grows into tall screens instead of idling at 300px and shrinks on short
    // ones instead of pushing the transport under the fold. Width is separately
    // capped to the column by PlayerCover (min(coverSize, 100%)). Listen's
    // column has far less chrome than Run's cockpit (no target block/presets),
    // hence the smaller subtraction and the higher cap.
    const desktopCoverSize = "clamp(160px, calc(100dvh - 470px - var(--run-topbar, 0px)), 460px)";
    return (
      <div className="run-desktop">
        {glowLayer}
        <PageHeader
          title="Listen"
          subtitle="Regular player — your music at native tempo, no cadence lock."
        />
        {!current && (
          // Pre-playback there's no queue worth a column — keep the picker and
          // empty state as a compact centered block until something starts.
          <div style={{ maxWidth: 640, margin: "0 auto" }}>
            <div style={{ marginBottom: 18 }}>{sourcePicker}</div>
            {emptyState}
          </div>
        )}
        {current && (
          <div className="run-desktop-body">
            <div className="run-player-col">
              <div className="listen-cockpit">
                {!isPreview && (
                  <div style={{ marginBottom: 18 }}>
                    <PlayerCover path={current.path} coverSize={desktopCoverSize} />
                  </div>
                )}
                {titleBlock}
              </div>
              {transportBlock}
              {modeRow}
            </div>
            <aside className="run-queue-col">{queuePanel("column")}</aside>
          </div>
        )}
        {lyricsDrawer}
      </div>
    );
  }

  // Mobile: one screen, no page scroll — Run's fill machinery with a bottom
  // Playing/Queue switcher. Tabs only exist once something is loaded; before
  // that the body is just the picker + empty state.
  const tabs = current && (
    <div data-testid="listen-tabs" className="segmented" style={{ display: "flex", marginTop: 10, width: "100%", flexShrink: 0 }}>
      {(["playing", "queue"] as const).map((v) => (
        <button
          key={v}
          className={"segmented-btn " + (view === v ? "active" : "")}
          style={{ flex: 1, minHeight: 34 }}
          onClick={() => setView(v)}
          aria-pressed={view === v}
        >
          {v === "playing" ? "Playing" : `Queue · ${player.orderedQueue.length}`}
        </button>
      ))}
    </div>
  );

  return (
    <div className={"run-mobile-fill listen-fill" + (view === "queue" && current ? " run-queue-open" : "")}>
      {glowLayer}
      <div className="run-mobile-body">
        {!current && (
          <>
            {sourcePicker}
            {emptyState}
          </>
        )}
        {current && view === "playing" && (
          <>
            {!isPreview && (
              <div className="run-cover-slot" data-testid="cover-slot">
                <PlayerCover path={current.path} fillHeight />
              </div>
            )}
            <div style={{ marginTop: 10 }}>{titleBlock}</div>
            {modeRow}
          </>
        )}
        {current && view === "queue" && queuePanel("fill")}
      </div>
      {transportBlock}
      {tabs}
      {lyricsDrawer}
    </div>
  );
}
