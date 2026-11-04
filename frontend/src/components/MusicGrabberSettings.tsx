import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import { Toggle } from "./Toggle";

type SettingsMap = Record<string, unknown>;

/** Settings → Music Grabber: connect a separate Music Grabber so missing tracks
 *  can be requested from playlists and the Related panel. The key is sent once
 *  and comes back masked; BPM Tagger's server makes every call. */
export default function MusicGrabberSettings() {
  const qc = useQueryClient();
  const settingsQ = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<{ settings: SettingsMap; env_locked?: string[] }>("/api/settings"),
  });
  const s = settingsQ.data?.settings ?? {};
  const [url, setUrl] = useState("");
  const [pub, setPub] = useState("");
  const [key, setKey] = useState("");
  const [members, setMembers] = useState(true);
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!settingsQ.data) return;
    setUrl(String(s.music_grabber_url ?? ""));
    setPub(String(s.music_grabber_public_url ?? ""));
    setKey(String(s.music_grabber_api_key ?? ""));
    setMembers(s.music_grabber_members_request !== false);
  }, [settingsQ.data]);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await api.post<{ configured: boolean }>("/api/grabber/settings", {
        music_grabber_url: url, music_grabber_public_url: pub, music_grabber_api_key: key,
        music_grabber_members_request: members, clear_key: key === "",
      });
      setMsg({ text: r.configured ? "Saved" : "Saved (not connected: needs an address and a key)", ok: true });
      qc.invalidateQueries({ queryKey: ["settings"] });
      qc.invalidateQueries({ queryKey: ["grabber-status"] });
    } catch (err) {
      setMsg({ text: err instanceof ApiError ? err.message : "Save failed", ok: false });
    } finally {
      setBusy(false);
    }
  }

  async function test() {
    setBusy(true);
    setMsg({ text: "Testing…", ok: true });
    try {
      await api.post("/api/grabber/test", {});
      setMsg({ text: "✓ Connected: the key works and can request tracks", ok: true });
    } catch (err) {
      setMsg({ text: err instanceof ApiError ? err.message : "Test failed", ok: false });
    } finally {
      setBusy(false);
    }
  }

  const label = (text: string, hint?: string) => (
    <div>
      <div className="field-row-label">{text}</div>
      {hint && <div className="field-row-hint">{hint}</div>}
    </div>
  );

  return (
    <form onSubmit={save}>
      <div className="settings-fields">
        <div className="field-row">
          {label("Address", "How this server reaches Music Grabber, e.g. http://music-grabber:8099")}
          <input type="text" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="http://music-grabber:8099"
            aria-label="Music Grabber address" style={{ maxWidth: 340, width: "100%", fontFamily: "var(--mono)", fontSize: 12 }} />
        </div>
        <div className="field-row">
          {label("Public address", "Optional: the link people open (its Inbox, for example). Defaults to the address above.")}
          <input type="text" value={pub} onChange={(e) => setPub(e.target.value)} placeholder="https://grabber.example.com"
            aria-label="Music Grabber public address" style={{ maxWidth: 340, width: "100%", fontFamily: "var(--mono)", fontSize: 12 }} />
        </div>
        <div className="field-row">
          {label("API key", "Create one in Music Grabber (Account → API keys) with the “request” scope.")}
          <input type="password" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" placeholder="mgk_…"
            aria-label="Music Grabber API key" style={{ maxWidth: 340, width: "100%", fontFamily: "var(--mono)", fontSize: 12 }} />
        </div>
        <div className="field-row">
          {label("Members can request", "Off: only you can ask Music Grabber for tracks.")}
          <Toggle on={members} onChange={setMembers} label="Members can request" />
        </div>
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginTop: 12 }}>
        <button type="submit" className="btn btn-primary btn-sm" disabled={busy}>Save</button>
        <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={test} title="Tests the saved settings">Test connection</button>
        {msg && <span role="status" style={{ fontSize: 12, color: msg.ok ? "var(--ok-fg)" : "var(--err-fg)" }}>{msg.text}</span>}
      </div>
    </form>
  );
}
