import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Toggle } from "./Toggle";

// Optional Subsonic API (docs/plans/subsonic-api.md): the on/off switch and the
// admin account's Subsonic credentials. The server returns a generated key or
// password exactly once, so this card shows it until the page is left and never
// fetches it again.

interface SubsonicStatus {
  enabled: boolean;
  active: boolean;
  allow_plain_password: boolean;
  username: string;
  has_api_key: boolean;
  has_password: boolean;
  last_used_at: string | null;
}

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
    <div style={{ marginTop: 8, padding: "8px 10px", border: "1px solid var(--border)", borderRadius: 8, maxWidth: 520 }}>
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

export default function SubsonicSettings() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["subsonic"], queryFn: () => api.get<SubsonicStatus>("/api/subsonic") });
  const [shown, setShown] = useState<{ apiKey?: string; password?: string }>({});
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

  async function generate(kind: "api-key" | "password") {
    const existing = kind === "api-key" ? s?.has_api_key : s?.has_password;
    if (existing && !window.confirm("Replace the current one? Clients using it will stop working until updated.")) return;
    setErr("");
    try {
      const r = await api.post<{ api_key?: string; password?: string }>(`/api/subsonic/${kind}`, {});
      setShown((cur) => (kind === "api-key" ? { ...cur, apiKey: r.api_key } : { ...cur, password: r.password }));
      await qc.invalidateQueries({ queryKey: ["subsonic"] });
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not generate");
    }
  }

  async function revoke(kind: "api-key" | "password") {
    if (!window.confirm("Revoke it? Clients using it will be signed out.")) return;
    await api.del(`/api/subsonic/${kind}`, {});
    setShown((cur) => (kind === "api-key" ? { ...cur, apiKey: undefined } : { ...cur, password: undefined }));
    await qc.invalidateQueries({ queryKey: ["subsonic"] });
  }

  if (!s) return null;
  const restartPending = s.enabled !== s.active;
  const serverUrl = window.location.origin;

  return (
    <div className="settings-fields">
      <div className="field-row">
        <Label label="Enable Subsonic API"
               hint="Serves /rest to Subsonic clients (Symfonium, Feishin, DSub…). Takes effect after a restart." />
        <Toggle on={s.enabled} onChange={(v) => void save({ subsonic_enabled: v })} label="Enable Subsonic API" />
      </div>
      {restartPending && (
        <div style={{ fontSize: 12, color: "var(--warn, var(--muted))" }}>
          {s.enabled ? "Enabled: restart to start serving /rest." : "Disabled: restart to stop serving /rest."}
        </div>
      )}
      <div className="field-row">
        <Label label="Client setup"
               hint="Server address and username to enter in the client. Use the API key if the client supports it, else the Subsonic password." />
        <div style={{ fontFamily: "var(--mono)", fontSize: 12, lineHeight: 1.7 }}>
          <div>{serverUrl}</div>
          <div>user: {s.username}</div>
        </div>
      </div>
      <div className="field-row">
        <Label label="API key" hint="OpenSubsonic key auth: the safest option, and the only one some clients offer." />
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <span style={{ fontSize: 12, color: "var(--muted)" }}>{s.has_api_key ? "Set" : "Not set"}</span>
          <button className="btn btn-primary btn-sm" type="button" onClick={() => void generate("api-key")}>
            {s.has_api_key ? "Regenerate" : "Generate"}
          </button>
          {s.has_api_key && <button className="btn btn-ghost btn-sm" type="button" onClick={() => void revoke("api-key")}>Revoke</button>}
        </div>
      </div>
      {shown.apiKey && <Secret label="API key" value={shown.apiKey} />}
      <div className="field-row">
        <Label label="Subsonic password"
               hint="For clients without API-key support. Random and separate from your web password." />
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <span style={{ fontSize: 12, color: "var(--muted)" }}>{s.has_password ? "Set" : "Not set"}</span>
          <button className="btn btn-primary btn-sm" type="button" onClick={() => void generate("password")}>
            {s.has_password ? "Regenerate" : "Generate"}
          </button>
          {s.has_password && <button className="btn btn-ghost btn-sm" type="button" onClick={() => void revoke("password")}>Revoke</button>}
        </div>
      </div>
      {shown.password && <Secret label="Subsonic password" value={shown.password} />}
      <div className="field-row">
        <Label label="Allow plain passwords anywhere"
               hint="Off: clients that send the password itself (not a token) are only accepted over https or from your local network." />
        <Toggle on={s.allow_plain_password} onChange={(v) => void save({ subsonic_allow_plain_password: v })}
                label="Allow plain passwords anywhere" />
      </div>
      {s.last_used_at && (
        <div style={{ fontSize: 12, color: "var(--muted)" }}>Last used {new Date(s.last_used_at).toLocaleString()}</div>
      )}
      {err && <div style={{ fontSize: 12, color: "var(--danger, #c33)" }}>{err}</div>}
    </div>
  );
}
