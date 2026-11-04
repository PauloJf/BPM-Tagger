import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";
import type { SettingsMap } from "../../lib/types";

/** Shared pieces of the Settings cards. Every card reads the one cached
 *  /api/settings query, seeds its own form state from it, and saves its own
 *  section, so a card can live (and be tested) on its own. */

export type Saved = "" | "saving" | "ok" | "err";

export function SaveButton({ state, label, ghost }: { state: Saved; label: string; ghost?: boolean }) {
  return (
    <button type="submit" className={"btn btn-sm " + (ghost ? "btn-ghost" : "btn-primary")} disabled={state === "saving"}>
      {state === "saving" ? "Saving…" : state === "ok" ? "Saved ✓" : label}
    </button>
  );
}

export function fieldLabel(label: React.ReactNode, hint?: string) {
  return (
    <div>
      <div className="field-row-label">{label}</div>
      {hint && <div className="field-row-hint">{hint}</div>}
    </div>
  );
}

/** The settings map (undefined while loading) and which keys an env var locks. */
export function useSettings() {
  const q = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<{ settings: SettingsMap; env_locked?: string[] }>("/api/settings"),
  });
  const envLocked = q.data?.env_locked ?? [];
  return { cfg: q.data?.settings, isLocked: (key: string) => envLocked.includes(key), isLoading: q.isLoading };
}

/** POST a section and flash its Save button; refreshes the settings query. */
export function useSaveSection() {
  const qc = useQueryClient();
  return async (path: string, body: unknown, setSaved: (s: Saved) => void) => {
    setSaved("saving");
    try {
      await api.post(path, body);
      qc.invalidateQueries({ queryKey: ["settings"] });
      setSaved("ok");
      setTimeout(() => setSaved(""), 1800);
    } catch {
      setSaved("err");
    }
  };
}

/** Typed readers over the settings map, with defaults for absent keys. */
export function readers(cfg: SettingsMap) {
  return {
    s: (k: string, d = "") => (cfg[k] == null ? d : String(cfg[k])),
    n: (k: string, d: number) => (cfg[k] == null ? d : Number(cfg[k])),
    b: (k: string, d: boolean) => (cfg[k] == null ? d : Boolean(cfg[k])),
  };
}

/** A "Test" button's result per connection kind. */
export function useTestConn() {
  const [testMsg, setTestMsg] = useState<Record<string, { ok: boolean; text: string }>>({});
  async function testConn(kind: string, path: string, body: unknown) {
    setTestMsg((m) => ({ ...m, [kind]: { ok: false, text: "Testing…" } }));
    try {
      const r = await api.post<{ ok: boolean; message?: string; error?: string }>(path, body);
      setTestMsg((m) => ({ ...m, [kind]: { ok: !!r.ok, text: r.ok ? (r.message || "OK") : (r.error || "Failed") } }));
    } catch (e) {
      setTestMsg((m) => ({ ...m, [kind]: { ok: false, text: e instanceof Error ? e.message : "Failed" } }));
    }
  }
  return { testMsg, testConn };
}

export function fmtBytes(n: number): string {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB"];
  let v = n, i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(i ? 1 : 0)} ${u[i]}`;
}
