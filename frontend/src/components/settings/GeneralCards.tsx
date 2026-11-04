import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { ACCENT_PRESETS, DEFAULT_ACCENT_HUE, accentSwatch, applyAccentHue, initialAccentHue } from "../../lib/accent";
import { Toggle } from "../Toggle";
import { SaveButton, fieldLabel, readers, useSaveSection, useSettings, type Saved } from "./shared";

/** Settings → General tab: appearance (per account), artwork and lyrics. */

interface LyricsFillStatus {
  running: boolean; total: number; done: number; filled: number; not_found: number;
}

export function AppearanceCard() {
  const [accentHue, setAccentHueState] = useState(() => initialAccentHue());
  // Persist the accent to the account too (follows the user across devices).
  // Applied locally at once; the server write is debounced so dragging the hue
  // slider doesn't spray POSTs.
  const accentSaveTimer = useRef<number | null>(null);
  const setAccent = (hue: number) => {
    applyAccentHue(hue);
    setAccentHueState(hue);
    if (accentSaveTimer.current) window.clearTimeout(accentSaveTimer.current);
    accentSaveTimer.current = window.setTimeout(() => {
      api.post("/api/accent", { hue: Math.round(hue) }).catch(() => { /* stays local */ });
    }, 400);
  };
  useEffect(() => () => {
    if (accentSaveTimer.current) window.clearTimeout(accentSaveTimer.current);
  }, []);

  return (
    <>
      {/* Appearance */}
      <div id="sec-appearance" className="settings-card card">
        <div className="settings-card-header">
          <h2>Appearance</h2>
          <p>Accent color used across the interface — logo, buttons, focus rings, progress bars, and the login screen. Saved to your account, so it follows you across browsers and devices.</p>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 18 }}>
          {ACCENT_PRESETS.map((p) => {
            const active = Math.round(accentHue) === p.hue;
            return (
              <button
                key={p.hue}
                type="button"
                onClick={() => setAccent(p.hue)}
                title={p.name}
                aria-label={p.name}
                aria-pressed={active}
                style={{
                  width: 34,
                  height: 34,
                  borderRadius: 10,
                  cursor: "pointer",
                  background: accentSwatch(p.hue),
                  border: "2px solid transparent",
                  boxShadow: active ? "0 0 0 2px var(--surface), 0 0 0 4px var(--text)" : "none",
                }}
              />
            );
          })}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12, maxWidth: 440 }}>
          <div className="slider-wrap" style={{ flex: 1 }}>
            <input
              type="range"
              min={0}
              max={360}
              step={1}
              value={Math.round(accentHue)}
              onChange={(e) => setAccent(+e.target.value)}
              aria-label="Accent hue"
            />
            <span className="slider-val">{Math.round(accentHue)}°</span>
          </div>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setAccent(DEFAULT_ACCENT_HUE)}>
            Reset
          </button>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 18 }}>
          <button type="button" className="btn btn-primary btn-sm" style={{ pointerEvents: "none" }}>
            Primary
          </button>
          <span className="badge badge--accent">Accent</span>
          <div style={{ flex: 1, maxWidth: 120, height: 8, borderRadius: 4, background: "linear-gradient(90deg, var(--accent), var(--accent-2))" }} />
        </div>
      </div>
    </>
  );
}

export function ArtworkCard() {
  const { cfg } = useSettings();
  const saveSection = useSaveSection();
  const [fetchArtistImages, setFetchArtistImages] = useState(false);
  const [artistImagesToLibrary, setArtistImagesToLibrary] = useState(false);
  const [artworkSaved, setArtworkSaved] = useState<Saved>("");
  useEffect(() => {
    if (!cfg) return;
    const { b } = readers(cfg);
    setFetchArtistImages(b("fetch_artist_images", false));
    setArtistImagesToLibrary(b("artist_images_to_library", false));
  }, [cfg]);

  return (
    <>
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
    </>
  );
}

export function LyricsCard() {
  const qc = useQueryClient();
  const { cfg } = useSettings();
  const saveSection = useSaveSection();
  const [lyricsCfg, setLyricsCfg] = useState({ mode: "embed" });
  const [lyricsSaved, setLyricsSaved] = useState<Saved>("");
  const [lyricsRetryNotFound, setLyricsRetryNotFound] = useState(false);
  useEffect(() => {
    if (!cfg) return;
    const { s } = readers(cfg);
    setLyricsCfg({ mode: s("lyrics_mode", "embed") || "embed" });
  }, [cfg]);

  // Bulk lyrics fill: poll while it runs.
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

  return (
    <>
      {/* Lyrics */}
      <div id="sec-lyrics" className="settings-card card">
        <div className="settings-card-header">
          <h2>Lyrics</h2>
          <p>Lyrics come from <a href="https://lrclib.net" target="_blank" rel="noreferrer" style={{ color: "var(--accent-2)" }}>LRCLIB</a> — a free, community-run lyrics database (no account needed). Synced (LRC) lyrics are preferred when available; Navidrome and most players pick them up.</p>
        </div>
        <form onSubmit={(e) => { e.preventDefault(); saveSection("/api/settings/lyrics", { lyrics_mode: lyricsCfg.mode }, setLyricsSaved); }}>
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
    </>
  );
}
