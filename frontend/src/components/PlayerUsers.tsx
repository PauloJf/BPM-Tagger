import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import type { PlayerUser, Playlist } from "../lib/types";
import { Toggle } from "./Toggle";

/** Settings → Player Access → Users (Phase 5). Admin-only CRUD for the local player
 *  accounts that log into Run mode: create/delete, reset password, full-access toggle,
 *  enable/disable, and per-user playlist scoping. */
export default function PlayerUsers() {
  const qc = useQueryClient();
  const usersQ = useQuery({ queryKey: ["players"], queryFn: () => api.get<{ players: PlayerUser[] }>("/api/players") });
  const playlistsQ = useQuery({ queryKey: ["playlists"], queryFn: () => api.get<{ playlists: Playlist[] }>("/api/playlists") });
  const users = usersQ.data?.players ?? [];
  const playlists = playlistsQ.data?.playlists ?? [];

  const invalidate = () => qc.invalidateQueries({ queryKey: ["players"] });

  // ── create form ──
  const [nu, setNu] = useState("");
  const [np, setNp] = useState("");
  const [nFull, setNFull] = useState(false);
  const [nIds, setNIds] = useState<number[]>([]);
  const [createErr, setCreateErr] = useState("");

  const createMut = useMutation({
    mutationFn: () => api.post("/api/players", {
      username: nu, password: np, full_access: nFull, playlist_ids: nIds,
    }),
    onSuccess: () => {
      setNu(""); setNp(""); setNFull(false); setNIds([]); setCreateErr("");
      invalidate();
    },
    onError: (e) => setCreateErr(e instanceof ApiError ? e.message : "Failed to create user"),
  });

  function toggleId(list: number[], id: number): number[] {
    return list.includes(id) ? list.filter((x) => x !== id) : [...list, id];
  }

  return (
    <div style={{ marginTop: 18, borderTop: "1px solid var(--border)", paddingTop: 16 }}>
      <h3 style={{ fontSize: 14, margin: "0 0 4px" }}>Run users</h3>
      <p style={{ fontSize: 12, color: "var(--muted)", margin: "0 0 12px", lineHeight: 1.5 }}>
        Named accounts that sign in with a username + password. A <strong>full-access</strong> user
        can run the whole library, starred tracks, and every playlist; a restricted user can only run
        the playlists you check for them. (The run password above is a shared, full-access guest.)
      </p>

      {/* existing users */}
      {users.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 16 }}>
          {users.map((u) => (
            <UserRow key={u.id} user={u} playlists={playlists} onChanged={invalidate} />
          ))}
        </div>
      )}

      {/* create */}
      <form
        onSubmit={(e) => { e.preventDefault(); createMut.mutate(); }}
        style={{ border: "1px solid var(--border)", borderRadius: 10, padding: 12, background: "var(--surface)" }}
      >
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 10 }}>Add a run user</div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
          <input
            type="text" value={nu} onChange={(e) => setNu(e.target.value)} placeholder="username"
            autoComplete="off" aria-label="New user username"
            style={{ flex: "1 1 160px", minWidth: 140, fontSize: 13 }}
          />
          <input
            type="password" value={np} onChange={(e) => setNp(e.target.value)} placeholder="password (min 8)"
            autoComplete="new-password" aria-label="New user password"
            style={{ flex: "1 1 160px", minWidth: 140, fontSize: 13 }}
          />
        </div>
        <div className="field-row" style={{ marginBottom: nFull ? 0 : 10 }}>
          <span style={{ fontSize: 13 }}>Full access <span style={{ color: "var(--muted)" }}>(library + all playlists)</span></span>
          <Toggle on={nFull} onChange={setNFull} label="Full access" />
        </div>
        {!nFull && (
          <PlaylistChecks playlists={playlists} selected={nIds} onToggle={(id) => setNIds((s) => toggleId(s, id))} />
        )}
        {createErr && <div style={{ color: "var(--err-fg)", fontSize: 12, marginTop: 8 }}>{createErr}</div>}
        <div style={{ marginTop: 12 }}>
          <button type="submit" className="btn btn-sm btn-primary" disabled={createMut.isPending}>
            {createMut.isPending ? "Adding…" : "Add user"}
          </button>
        </div>
      </form>
    </div>
  );
}

function UserRow({ user, playlists, onChanged }: {
  user: PlayerUser; playlists: Playlist[]; onChanged: () => void;
}) {
  const [expand, setExpand] = useState(false);
  const [resetPw, setResetPw] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.patch(`/api/players/${user.id}`, body),
    onSuccess: onChanged,
  });
  const resetMut = useMutation({
    mutationFn: () => api.post(`/api/players/${user.id}/password`, { new_password: resetPw }),
    onSuccess: () => { setResetPw(""); setMsg({ ok: true, text: "Password reset — the user is logged out everywhere." }); },
    onError: (e) => setMsg({ ok: false, text: e instanceof ApiError ? e.message : "Reset failed" }),
  });
  const delMut = useMutation({
    mutationFn: () => api.del(`/api/players/${user.id}`),
    onSuccess: onChanged,
  });

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 10, padding: "10px 12px", background: "var(--surface)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span style={{ fontWeight: 600, fontSize: 14 }}>{user.username}</span>
        <span
          className="badge"
          style={{ fontSize: 10, padding: "1px 7px", background: "var(--bg)", color: user.full_access ? "var(--accent-2)" : "var(--muted)" }}
        >
          {user.full_access ? "full access" : `${user.playlist_ids.length} playlist${user.playlist_ids.length === 1 ? "" : "s"}`}
        </span>
        {!user.enabled && <span className="badge" style={{ fontSize: 10, padding: "1px 7px", color: "var(--err-fg)" }}>disabled</span>}
        <span style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <button className="btn btn-bare btn-sm" style={{ fontSize: 12, color: "var(--accent-2)" }} onClick={() => setExpand((x) => !x)}>
            {expand ? "Close" : "Edit"}
          </button>
          <button
            className="btn btn-bare btn-sm" style={{ fontSize: 12, color: "var(--err-fg)" }}
            onClick={() => { if (confirm(`Delete run user "${user.username}"?`)) delMut.mutate(); }}
          >Delete</button>
        </span>
      </div>

      {expand && (
        <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 12 }}>
          <div className="field-row">
            <span style={{ fontSize: 13 }}>Full access</span>
            <Toggle on={user.full_access} onChange={(v) => patch.mutate({ full_access: v })} label="Full access" />
          </div>
          <div className="field-row">
            <span style={{ fontSize: 13 }}>Enabled</span>
            <Toggle on={user.enabled} onChange={(v) => patch.mutate({ enabled: v })} label="Enabled" />
          </div>
          {!user.full_access && (
            <PlaylistChecks
              playlists={playlists}
              selected={user.playlist_ids}
              onToggle={(id) => patch.mutate({
                playlist_ids: user.playlist_ids.includes(id)
                  ? user.playlist_ids.filter((x) => x !== id)
                  : [...user.playlist_ids, id],
              })}
            />
          )}
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", borderTop: "1px solid var(--border)", paddingTop: 12 }}>
            <input
              type="password" value={resetPw} onChange={(e) => setResetPw(e.target.value)}
              placeholder="new password (min 8)" autoComplete="new-password" aria-label="Reset password"
              style={{ flex: "1 1 180px", minWidth: 150, fontSize: 13 }}
            />
            <button
              className="btn btn-sm btn-ghost" disabled={resetPw.length < 8 || resetMut.isPending}
              onClick={() => resetMut.mutate()}
            >Reset password</button>
          </div>
          {msg && <div style={{ fontSize: 12, color: msg.ok ? "var(--ok-fg)" : "var(--err-fg)" }}>{msg.text}</div>}
        </div>
      )}
    </div>
  );
}

function PlaylistChecks({ playlists, selected, onToggle }: {
  playlists: Playlist[]; selected: number[]; onToggle: (id: number) => void;
}) {
  if (playlists.length === 0) {
    return <div style={{ fontSize: 12, color: "var(--muted)" }}>No playlists yet — create one on the Playlists page.</div>;
  }
  return (
    <div>
      <div style={{ fontSize: 11, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 6 }}>Playlists</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {playlists.map((p) => (
          <label key={p.id} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer" }}>
            <input type="checkbox" checked={selected.includes(p.id)} onChange={() => onToggle(p.id)} />
            <span>{p.name}</span>
            <span style={{ fontSize: 11, color: "var(--muted)" }}>· {p.source}</span>
          </label>
        ))}
      </div>
    </div>
  );
}
