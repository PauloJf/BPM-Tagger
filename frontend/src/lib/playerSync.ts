// Cross-device player-state sync: mirrors the queue snapshot the player
// persists to localStorage up to the server (per account), and pulls it back
// on boot/foreground. Snapshot semantics, last writer wins — see the sync
// block in player.tsx for the adoption rules.
//
// Raw fetch() rather than the api wrapper on purpose: the pagehide push needs
// `keepalive` (which the wrapper doesn't expose), and a sync failure must stay
// silent — it must never bounce the app to the login screen the way the
// wrapper's 401 handler would mid-run.

import { getCsrfToken } from "./api";
import type { SavedPlayer } from "./player";

export interface ServerPlayerState {
  sync: boolean;                 // false → no account row (shared Guest) — stay browser-local
  state: SavedPlayer | null;
  updated_at: string | null;     // server write stamp (opaque; compared by equality only)
}

// The last server stamp this browser has seen (adopted or written). A GET whose
// stamp differs means another device wrote since we last looked. Deliberately a
// separate key from the snapshot itself so clearing one never corrupts the other.
const STAMP_KEY = "bpm.player.serverStamp";

export function lastSeenStamp(): string {
  try { return localStorage.getItem(STAMP_KEY) || ""; } catch { return ""; }
}

export function rememberStamp(stamp: string | null) {
  try {
    if (stamp) localStorage.setItem(STAMP_KEY, stamp);
    else localStorage.removeItem(STAMP_KEY);
  } catch { /* ignore */ }
}

export async function fetchServerState(): Promise<ServerPlayerState | null> {
  try {
    const resp = await fetch("/api/player/state", { credentials: "same-origin" });
    if (!resp.ok) return null;
    return (await resp.json()) as ServerPlayerState;
  } catch {
    return null;   // offline / server gone — sync just waits for the next chance
  }
}

/** Replace the account's server snapshot (null clears it). `keepalive` lets the
 *  pagehide flush survive the page teardown. Failures are silent — the next
 *  push wins. */
export async function pushServerState(state: SavedPlayer | null, keepalive = false): Promise<void> {
  try {
    const resp = await fetch("/api/player/state", {
      method: "PUT",
      credentials: "same-origin",
      keepalive,
      headers: { "Content-Type": "application/json", "X-CSRF-Token": getCsrfToken() },
      body: JSON.stringify({ state }),
    });
    if (resp.ok) {
      const body = await resp.json().catch(() => null);
      // Our own write's stamp counts as "seen" — otherwise the next GET would
      // read our own write as another device's and pointlessly re-adopt it.
      if (body && body.sync) rememberStamp(body.updated_at ?? null);
    }
  } catch { /* ignore */ }
}
