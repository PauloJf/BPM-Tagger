import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import type { Playlist } from "../lib/types";

/** Per-track outcome counts, shared by both bulk modes. */
type Counts = { added: number; already_present: number; skipped_missing: number };

/** Trigger + popover for adding tracks to a Local playlist.
 *
 *  Three modes, same UI (a list of the user's Local playlists + an inline
 *  "New playlist…" create). Pass exactly one of them:
 *   - default (`path`): add one library track — posts to
 *     /api/playlists/<id>/tracks (sets the row to 'have' directly).
 *   - bulk (`importFrom`): copy every library-backed track from another playlist —
 *     posts to /api/playlists/<id>/import; the toast reports added / already-there /
 *     not-in-library counts. The source playlist is filtered out of the target list
 *     so it can't be imported into itself.
 *   - bulk (`paths`): add an arbitrary list of library paths, in order — posts
 *     the same /api/playlists/<id>/tracks endpoint with {paths} and reports the
 *     same counts. This is what saves a run queue as a playlist.
 *
 *  Playlist *management* is admin-only, so callers must not render this in the
 *  player role — the backend 403s it regardless.
 *
 *  The popover is portaled to <body> and fixed-positioned from the trigger's rect
 *  so it never clips inside a table row or a `<Link>`, and it clamps to the
 *  viewport so it stays on-screen on narrow phones. */
export default function AddToPlaylistMenu({
  path, paths, importFrom, heading = "Add to playlist", label,
  className, style, title = "Add to playlist", iconSize = 12,
}: {
  path?: string;
  paths?: string[];
  importFrom?: number;
  heading?: string;
  label?: string;      // when set, the trigger shows this text beside the icon
  className?: string;
  style?: React.CSSProperties;
  title?: string;
  iconSize?: number;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [status, setStatus] = useState<{ text: string; ok: boolean } | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  const playlistsQ = useQuery({
    queryKey: ["playlists"],
    queryFn: () => api.get<{ playlists: Playlist[] }>("/api/playlists"),
    enabled: open,
    staleTime: 10_000,
  });
  const locals = (playlistsQ.data?.playlists ?? [])
    .filter((p) => p.source === "local" && p.id !== importFrom);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["playlists"] });
    qc.invalidateQueries({ queryKey: ["run-playlists"] });
    qc.invalidateQueries({ queryKey: ["playlist-tracks"] });
  };

  // Apply the pick to one (existing or freshly-created) local playlist, returning
  // the status message. Single-track add vs. bulk import diverge only here.
  async function pick(id: number, name: string): Promise<{ ok: boolean; text: string }> {
    // Both bulk modes answer with the same counts shape, so they share a toast.
    const countsToast = (c: Counts) => {
      const parts = [`Added ${c.added}`];
      if (c.already_present) parts.push(`${c.already_present} already there`);
      if (c.skipped_missing) parts.push(`${c.skipped_missing} not in library`);
      return { ok: true, text: `${name}: ${parts.join(" · ")}` };
    };
    if (importFrom != null) {
      const res = await api.post<{ counts: Counts }>(
        `/api/playlists/${id}/import`, { from_playlist_id: importFrom });
      return countsToast(res.counts);
    }
    if (paths) {
      const res = await api.post<{ counts: Counts }>(`/api/playlists/${id}/tracks`, { paths });
      return countsToast(res.counts);
    }
    const res = await api.post<{ added: boolean }>(`/api/playlists/${id}/tracks`, { path });
    return { ok: true, text: res.added ? `Added to ${name}` : `Already in ${name}` };
  }

  const addTo = useMutation({
    mutationFn: (id: number) => pick(id, locals.find((p) => p.id === id)?.name ?? "playlist"),
    onSuccess: (res) => { setStatus(res); invalidate(); },
    onError: (e) => setStatus({ ok: false, text: e instanceof ApiError ? e.message : "Failed to add" }),
  });

  const createAndAdd = useMutation({
    mutationFn: async (name: string) => {
      const r = await api.post<{ playlist: Playlist }>("/api/playlists", { source: "local", name });
      return pick(r.playlist.id, r.playlist.name);
    },
    onSuccess: (res) => {
      setStatus(res);
      setCreating(false);
      setNewName("");
      invalidate();
    },
    onError: (e) => setStatus({ ok: false, text: e instanceof ApiError ? e.message : "Failed to create" }),
  });

  // Position the popover under the trigger, clamped to the viewport.
  useLayoutEffect(() => {
    if (!open || !btnRef.current) return;
    const r = btnRef.current.getBoundingClientRect();
    const width = Math.min(248, window.innerWidth - 16);
    const left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8));
    const top = Math.min(r.bottom + 6, window.innerHeight - 8);
    setPos({ top, left });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  function toggle(e: React.MouseEvent) {
    // These triggers live inside <Link> rows — don't navigate or bubble.
    e.preventDefault();
    e.stopPropagation();
    setStatus(null);
    setCreating(false);
    setOpen((o) => !o);
  }

  const width = Math.min(248, typeof window !== "undefined" ? window.innerWidth - 16 : 248);

  const menu = open && pos && createPortal(
    <>
      {/* Transparent full-screen catcher closes the menu on any outside click. */}
      <div
        style={{ position: "fixed", inset: 0, zIndex: 200 }}
        onClick={(e) => { e.preventDefault(); e.stopPropagation(); setOpen(false); }}
      />
      <div
        role="menu"
        onClick={(e) => { e.preventDefault(); e.stopPropagation(); }}
        style={{
          position: "fixed", top: pos.top, left: pos.left, width, zIndex: 201,
          background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 12,
          boxShadow: "0 18px 50px -18px rgba(0,0,0,0.6)", padding: 6, maxHeight: "60vh",
          overflowY: "auto", display: "flex", flexDirection: "column", gap: 2,
        }}
      >
        <div style={{ fontFamily: "var(--mono)", fontSize: 10, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--muted)", padding: "4px 8px" }}>
          {heading}
        </div>

        {playlistsQ.isLoading ? (
          <div style={{ fontSize: 12, color: "var(--muted)", padding: "6px 8px" }}>Loading…</div>
        ) : locals.length === 0 && !creating ? (
          <div style={{ fontSize: 12, color: "var(--muted)", padding: "6px 8px" }}>No local playlists yet.</div>
        ) : (
          locals.map((p) => (
            <button
              key={p.id}
              role="menuitem"
              className="btn btn-bare btn-sm"
              disabled={addTo.isPending}
              style={{ justifyContent: "flex-start", width: "100%", textAlign: "left", padding: "6px 8px", fontSize: 13 }}
              onClick={() => addTo.mutate(p.id)}
              title={`Add to “${p.name}”`}
            >
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.name}</span>
            </button>
          ))
        )}

        {creating ? (
          <div style={{ display: "flex", gap: 6, padding: "4px 6px" }}>
            <input
              autoFocus
              type="text"
              value={newName}
              placeholder="Playlist name"
              maxLength={120}
              style={{ flex: 1, minWidth: 0, fontSize: 13, padding: "6px 8px" }}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && newName.trim()) createAndAdd.mutate(newName.trim());
                if (e.key === "Escape") { e.stopPropagation(); setCreating(false); }
              }}
            />
            <button
              className="btn btn-primary btn-sm"
              disabled={!newName.trim() || createAndAdd.isPending}
              onClick={() => createAndAdd.mutate(newName.trim())}
            >
              {createAndAdd.isPending ? "…" : "Add"}
            </button>
          </div>
        ) : (
          <button
            role="menuitem"
            className="btn btn-bare btn-sm"
            style={{ justifyContent: "flex-start", width: "100%", textAlign: "left", padding: "6px 8px", fontSize: 13, color: "var(--accent-2)", borderTop: "1px solid var(--border)", borderRadius: 0, marginTop: 2 }}
            onClick={() => { setStatus(null); setCreating(true); }}
          >
            ＋ New playlist…
          </button>
        )}

        {status && (
          <div style={{ fontSize: 12, padding: "4px 8px", color: status.ok ? "var(--ok-fg)" : "var(--err-fg)" }}>
            {status.text}
          </div>
        )}
      </div>
    </>,
    document.body,
  );

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        className={className}
        style={style}
        aria-label={title}
        aria-haspopup="menu"
        aria-expanded={open}
        title={title}
        onClick={toggle}
      >
        <svg width={iconSize} height={iconSize} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={label ? { marginRight: 5 } : undefined}>
          <path d="M3 6h11M3 12h8M3 18h8" />
          <path d="M18 9v9M13.5 13.5h9" />
        </svg>
        {label}
      </button>
      {menu}
    </>
  );
}
