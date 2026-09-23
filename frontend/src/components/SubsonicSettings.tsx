import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Toggle } from "./Toggle";

// Optional Subsonic API (docs/plans/subsonic-api.md): the on/off switch and each
// account's Subsonic credentials. The server returns a generated key or password
// exactly once, so this card shows it until the page is left and never fetches
// it again. Having credentials is an account's Subsonic access: player users
// have none until generated here, and then see only their playlists' tracks.

interface Account {
  owner: string;
  username: string;
  kind: "admin" | "player";
  enabled: boolean;
  has_api_key: boolean;
  has_password: boolean;
  last_used_at: string | null;
}

interface SubsonicStatus {
  enabled: boolean;
  active: boolean;
  allow_plain_password: boolean;
  accounts: Account[];
}

type Kind = "api-key" | "password";

function Label({ label, hint }: { label: string; hint?: string }) {
  return (
    <div>
      <div className="field-row-label">{label}</div>
      {hint && <div className="field-row-hint">{hint}</div>}
    </div>
  );
}

function Secret({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div style={{ marginTop: 6, padding: "8px 10px", border: "1px solid var(--border)", borderRadius: 8, maxWidth: 520 }}>
      <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 4 }}>
        {label}: copy it now, it won't be shown again.
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <code style={{ fontFamily: "var(--mono)", fontSize: 12, wordBreak: "break-all", flex: "1 1 240px" }}>{value}</code>
        <button className="btn btn-ghost btn-sm" type="button"
                onClick={() => { void navigator.clipboard?.writeText(value).then(() => setCopied(true)); }}>
          {copied ? "Copied ✓" : "Copy"}
        </button>
      </div>
    </div>
  );
}

function CredButtons({ has, onGenerate, onRevoke, noun }: {
  has: boolean; onGenerate: () => void; onRevoke: () => void; noun: string;
}) {
  return (
    <span style={{ display: "inline-flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
      <button className="btn btn-ghost btn-sm" type="button" onClick={onGenerate}>
        {has ? `New ${noun}` : `Generate ${noun}`}
      </button>
      {has && <button className="btn btn-ghost btn-sm" type="button" onClick={onRevoke}>Revoke</button>}
    </span>
  );
}

export default function SubsonicSettings() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["subsonic"], queryFn: () => api.get<SubsonicStatus>("/api/subsonic") });
  // owner → secrets generated during this visit
  const [shown, setShown] = useState<Record<string, { apiKey?: string; password?: string }>>({});
  const [err, setErr] = useState("");
  const s = q.data;

  async function save(body: Partial<Record<"subsonic_enabled" | "subsonic_allow_plain_password", boolean>>) {
    setErr("");
    try {
      await api.post("/api/subsonic/settings", body);
      await qc.invalidateQueries({ queryKey: ["subsonic"] });
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not save");
    }
  }

  const path = (owner: string, kind: Kind) => `/api/subsonic/accounts/${encodeURIComponent(owner)}/${kind}`;

  async function generate(acct: Account, kind: Kind) {
    const existing = kind === "api-key" ? acct.has_api_key : acct.has_password;
    if (existing && !window.confirm("Replace the current one? Apps using it will stop working until updated.")) return;
    setErr("");
    try {
      const r = await api.post<{ api_key?: string; password?: string }>(path(acct.owner, kind), {});
      setShown((cur) => ({
        ...cur,
        [acct.owner]: { ...cur[acct.owner], ...(kind === "api-key" ? { apiKey: r.api_key } : { password: r.password }) },
      }));
      await qc.invalidateQueries({ queryKey: ["subsonic"] });
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not generate");
    }
  }

  async function revoke(acct: Account, kind: Kind) {
    if (!window.confirm("Revoke it? Apps using it will be signed out.")) return;
    await api.del(path(acct.owner, kind), {});
    setShown((cur) => ({
      ...cur,
      [acct.owner]: { ...cur[acct.owner], ...(kind === "api-key" ? { apiKey: undefined } : { password: undefined }) },
    }));
    await qc.invalidateQueries({ queryKey: ["subsonic"] });
  }

  if (!s) return null;
  const restartPending = s.enabled !== s.active;

  return (
    <div className="settings-fields">
      <div className="field-row">
        <Label label="Enable Subsonic API"
               hint="Serves /rest to Subsonic apps (Symfonium, Feishin, DSub…). Takes effect after a restart." />
        <Toggle on={s.enabled} onChange={(v) => void save({ subsonic_enabled: v })} label="Enable Subsonic API" />
      </div>
      {restartPending && (
        <div style={{ fontSize: 12, color: "var(--warn, var(--muted))" }}>
          {s.enabled ? "Enabled: restart to start serving /rest." : "Disabled: restart to stop serving /rest."}
        </div>
      )}
      <div className="field-row">
        <Label label="Server address" hint="Enter this in the app, with the account's username below." />
        <code style={{ fontFamily: "var(--mono)", fontSize: 12 }}>{window.location.origin}</code>
      </div>

      <div>
        <div className="field-row-label" style={{ marginBottom: 2 }}>Accounts</div>
        <div className="field-row-hint" style={{ marginBottom: 8 }}>
          Use the API key if the app supports it, else the Subsonic password. Both are separate from web passwords.
          Player users see only the tracks of their playlists, and can't edit playlists.
        </div>
        {s.accounts.map((a) => {
          const secrets = shown[a.owner] ?? {};
          const access = a.has_api_key || a.has_password;
          return (
            <div key={a.owner} style={{ padding: "8px 0", borderTop: "1px solid var(--border)" }}>
              <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                <span style={{ fontFamily: "var(--mono)", fontSize: 13, minWidth: 110 }}>{a.username}</span>
                <span className="badge badge--neutral" style={{ fontSize: 10, padding: "1px 6px" }}>
                  {a.kind === "admin" ? "admin · whole library" : "player · its playlists"}
                </span>
                {!a.enabled && <span className="badge badge--review" style={{ fontSize: 10, padding: "1px 6px" }}>disabled</span>}
                <span style={{ fontSize: 12, color: "var(--muted)" }}>
                  {access ? (a.last_used_at ? `last used ${new Date(a.last_used_at).toLocaleString()}` : "never used") : "no access"}
                </span>
              </div>
              <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 6 }}>
                <CredButtons has={a.has_api_key} noun="API key"
                             onGenerate={() => void generate(a, "api-key")} onRevoke={() => void revoke(a, "api-key")} />
                <CredButtons has={a.has_password} noun="password"
                             onGenerate={() => void generate(a, "password")} onRevoke={() => void revoke(a, "password")} />
              </div>
              {secrets.apiKey && <Secret label={`API key for ${a.username}`} value={secrets.apiKey} />}
              {secrets.password && <Secret label={`Subsonic password for ${a.username}`} value={secrets.password} />}
            </div>
          );
        })}
      </div>

      <div className="field-row">
        <Label label="Allow plain passwords anywhere"
               hint="Off: apps that send the password itself (not a token) are only accepted over https or from your local network." />
        <Toggle on={s.allow_plain_password} onChange={(v) => void save({ subsonic_allow_plain_password: v })}
                label="Allow plain passwords anywhere" />
      </div>
      {err && <div style={{ fontSize: 12, color: "var(--danger, #c33)" }}>{err}</div>}
    </div>
  );
}
