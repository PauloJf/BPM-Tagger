import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { updateState } from "../../lib/version";
import { fmtBytes } from "./shared";

/** Settings → Maintenance tab: trash, deleted records, version, restart. */

export function TrashCard() {
  const qc = useQueryClient();
  const trashQ = useQuery({ queryKey: ["trash"], queryFn: () => api.get<{ count: number; bytes: number }>("/api/trash") });
  const [purging, setPurging] = useState(false);
  async function purgeTrash() {
    if (!window.confirm("Permanently delete everything in the trash? This cannot be undone.")) return;
    setPurging(true);
    try { await api.post("/api/trash/purge", {}); qc.invalidateQueries({ queryKey: ["trash"] }); }
    finally { setPurging(false); }
  }

  return (
    <>
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
    </>
  );
}

export function DeletedCard() {
  const qc = useQueryClient();
  const deletedQ = useQuery({ queryKey: ["deleted"], queryFn: () => api.get<{ count: number }>("/api/deleted") });
  const [purgingDeleted, setPurgingDeleted] = useState(false);
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

  return (
    <>
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
    </>
  );
}

export function VersionCard() {
  const { version } = useAuth();
  const [versionMsg, setVersionMsg] = useState<{ text: string; color: string } | null>(null);
  async function checkLatest() {
    setVersionMsg({ text: "Checking…", color: "var(--muted)" });
    try {
      const d = await api.get<{ latest?: string; error?: string }>("/api/version/check");
      if (d.error) setVersionMsg({ text: `Error: ${d.error}`, color: "var(--err-fg)" });
      else if (!d.latest) setVersionMsg({ text: "No releases published yet", color: "var(--muted)" });
      else if (updateState(version, d.latest) === "update-available")
        setVersionMsg({ text: `Latest: ${d.latest} (you have v${version})`, color: "var(--warn-fg)" });
      else if (updateState(version, d.latest) === "ahead")
        setVersionMsg({ text: `✓ Up to date (v${version}; newest release is ${d.latest})`, color: "var(--ok-fg)" });
      else setVersionMsg({ text: `✓ Up to date (${d.latest})`, color: "var(--ok-fg)" });
    } catch {
      setVersionMsg({ text: "Network error", color: "var(--err-fg)" });
    }
  }

  return (
    <>
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
    </>
  );
}

export function RestartCard() {
  const [restartMsg, setRestartMsg] = useState<{ text: string; color: string } | null>(null);
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

  return (
    <>
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
    </>
  );
}
