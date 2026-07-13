import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { ImageCandidate } from "../lib/types";

interface Props {
  kind: "artist" | "album" | "track";
  title: string;
  /** Seed for the search box (artist name, "artist album", "artist title"…). */
  initialQuery: string;
  /** Apply the chosen image — a search-candidate URL or an uploaded file. */
  onPick: (pick: { url?: string; file?: File }) => Promise<void>;
  onClose: () => void;
  /** Optional "remove custom image" action (artist images). */
  onReset?: () => Promise<void>;
}

/** Modal picker for artist/album/track images: searches Spotify (when
 *  connected) + Deezer via /api/images/search, and accepts a pasted image URL
 *  or a local file upload. */
export function ImagePicker({ kind, title, initialQuery, onPick, onClose, onReset }: Props) {
  const [query, setQuery] = useState(initialQuery);
  const [candidates, setCandidates] = useState<ImageCandidate[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [urlInput, setUrlInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  async function search(q: string) {
    if (!q.trim()) return;
    setSearching(true);
    setError("");
    try {
      const r = await api.get<{ candidates: ImageCandidate[] }>(
        `/api/images/search?kind=${kind}&q=${encodeURIComponent(q.trim())}`);
      setCandidates(r.candidates || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Search failed");
      setCandidates([]);
    } finally {
      setSearching(false);
    }
  }

  // Search once with the seeded query when the dialog opens.
  useEffect(() => {
    search(initialQuery);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function apply(pick: { url?: string; file?: File }) {
    setBusy(true);
    setError("");
    try {
      await onPick(pick);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to apply image");
      setBusy(false);
      return;
    }
    setBusy(false);
  }

  return (
    <div
      role="dialog"
      aria-label={title}
      onClick={onClose}
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)", zIndex: 200, display: "grid", placeItems: "center", padding: 16 }}
    >
      <div className="card" onClick={(e) => e.stopPropagation()} style={{ width: "min(620px, 100%)", maxHeight: "84vh", overflowY: "auto" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
          <h2 style={{ fontSize: 15, fontWeight: 600, flex: 1 }}>{title}</h2>
          <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <form onSubmit={(e) => { e.preventDefault(); search(query); }} style={{ display: "flex", gap: 8, marginBottom: 12 }}>
          <input type="text" value={query} onChange={(e) => setQuery(e.target.value)}
            placeholder="Search Spotify / Deezer…" style={{ flex: 1 }} />
          <button className="btn btn-primary btn-sm" type="submit" disabled={searching || busy}>
            {searching ? "Searching…" : "Search"}
          </button>
        </form>

        {candidates !== null && candidates.length === 0 && !searching && (
          <p style={{ fontSize: 12, color: "var(--muted)", marginBottom: 12 }}>
            No results — refine the search, paste an image URL, or upload a file.
          </p>
        )}
        {candidates !== null && candidates.length > 0 && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(104px, 1fr))", gap: 10, marginBottom: 14 }}>
            {candidates.map((c) => (
              <button
                key={c.source + c.image_url}
                type="button"
                disabled={busy}
                onClick={() => apply({ url: c.image_url })}
                title={`${c.name}${c.detail ? ` — ${c.detail}` : ""} (${c.source})`}
                style={{ background: "none", border: "1px solid var(--border)", borderRadius: 10, padding: 6, cursor: busy ? "wait" : "pointer", textAlign: "center", color: "var(--text)" }}
              >
                <img src={c.thumb_url} alt="" loading="lazy"
                  style={{ width: "100%", aspectRatio: "1", objectFit: "cover", borderRadius: 6, display: "block" }} />
                <div style={{ fontSize: 10, marginTop: 5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.name}</div>
                <div style={{ fontSize: 9, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {c.detail || " "}
                </div>
                <span className="chip chip--neutral" style={{ marginTop: 4, fontSize: 9 }}>{c.source}</span>
              </button>
            ))}
          </div>
        )}

        <div style={{ borderTop: "1px solid var(--border)", paddingTop: 12, display: "flex", flexDirection: "column", gap: 10 }}>
          <form onSubmit={(e) => { e.preventDefault(); if (urlInput.trim()) apply({ url: urlInput.trim() }); }} style={{ display: "flex", gap: 8 }}>
            <input type="url" value={urlInput} onChange={(e) => setUrlInput(e.target.value)}
              placeholder="…or paste an image URL" style={{ flex: 1 }} />
            <button className="btn btn-ghost btn-sm" type="submit" disabled={busy || !urlInput.trim()}>Use URL</button>
          </form>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <input ref={fileRef} type="file" accept="image/*" style={{ display: "none" }}
              onChange={(e) => { const f = e.target.files?.[0]; if (f) apply({ file: f }); e.target.value = ""; }} />
            <button className="btn btn-ghost btn-sm" type="button" disabled={busy} onClick={() => fileRef.current?.click()}>
              Upload a file…
            </button>
            {onReset && (
              <button className="btn btn-danger btn-sm" type="button" disabled={busy}
                onClick={async () => { setBusy(true); try { await onReset(); } finally { setBusy(false); } }}>
                Remove custom image
              </button>
            )}
            <div style={{ flex: 1 }} />
            {busy && <span style={{ fontSize: 12, color: "var(--muted)" }}>Applying…</span>}
            {error && <span style={{ fontSize: 12, color: "var(--err-fg)" }}>{error}</span>}
          </div>
        </div>
      </div>
    </div>
  );
}
