import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

// Live list of Subsonic apps seen in the last hour and what each is playing
// (/api/subsonic/clients, backed by web/subsonic/activity.py — in memory, so a
// restart clears it). Polls while mounted; the host only mounts it while the
// API is actually being served.

export interface SubsonicClient {
  owner: string;
  username: string;
  app: string;
  version: string;
  ip: string;
  user_agent: string;
  first_seen: number;
  last_seen: number;
  requests: number;
  playing: null | {
    track_id: number; title: string; artist: string; album: string;
    duration_s: number | null; elapsed_s: number; source: "report" | "stream";
  };
  last_played: null | { track_id: number; title: string; artist: string; ago_s: number };
}

function ago(seconds: number): string {
  if (seconds < 45) return "just now";
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m} min ago`;
  return `${Math.round(m / 60)} h ago`;
}

function mmss(s: number): string {
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}

export default function SubsonicClients() {
  const q = useQuery({
    queryKey: ["subsonic-clients"],
    queryFn: () => api.get<{ now: number; clients: SubsonicClient[] }>("/api/subsonic/clients"),
    refetchInterval: 5000,
  });
  const clients = q.data?.clients ?? [];
  const now = q.data?.now ?? Date.now() / 1000;

  return (
    <div data-testid="subsonic-clients">
      <div className="field-row-label" style={{ marginBottom: 2 }}>Connected apps</div>
      <div className="field-row-hint" style={{ marginBottom: 8 }}>
        Apps that used the API in the last hour, and what they're playing. Live; not kept across restarts.
      </div>
      {clients.length === 0 ? (
        <div style={{ fontSize: 12, color: "var(--muted)", padding: "4px 0 8px" }}>
          {q.isLoading ? "Loading…" : "No app has connected in the last hour."}
        </div>
      ) : clients.map((c) => {
        const idle = now - c.last_seen;
        const p = c.playing;
        return (
          <div key={`${c.owner}|${c.app}|${c.ip}`} data-testid="subsonic-client"
               style={{ padding: "8px 0", borderTop: "1px solid var(--border)" }}>
            <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
              <span style={{ fontWeight: 600, fontSize: 13 }}>{c.app}</span>
              {c.version && <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)" }}>API {c.version}</span>}
              <span className="badge badge--neutral" style={{ fontSize: 10, padding: "1px 6px" }}>{c.username}</span>
              <span style={{ fontSize: 11, color: "var(--muted)", marginLeft: "auto" }}
                    title={c.user_agent || undefined}>
                {c.ip} · {idle < 45 ? "active now" : `seen ${ago(idle)}`}
              </span>
            </div>
            <div style={{ fontSize: 12, marginTop: 4, color: p ? "var(--text)" : "var(--muted)" }}>
              {p ? (
                <>
                  <span aria-hidden>▶ </span>
                  <span style={{ fontWeight: 500 }}>{p.title || "Untitled"}</span>
                  {p.artist && <span style={{ color: "var(--muted)" }}> — {p.artist}</span>}
                  <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)" }}>
                    {" "}· {mmss(Math.min(p.elapsed_s, p.duration_s ?? p.elapsed_s))}{p.duration_s ? ` / ${mmss(p.duration_s)}` : ""}
                  </span>
                  {p.source === "stream" && (
                    <span style={{ fontSize: 11, color: "var(--muted)" }}
                          title="This app doesn't report what it's playing, so this is the last track it streamed — it may be pre-loading ahead."> · from its last stream</span>
                  )}
                </>
              ) : c.last_played ? (
                <>Last played {c.last_played.title}{c.last_played.artist ? ` — ${c.last_played.artist}` : ""} · {ago(c.last_played.ago_s)}</>
              ) : (
                <>Browsing · nothing played yet</>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
