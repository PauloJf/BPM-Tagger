import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, ApiError, setCsrfToken, setUnauthorizedHandler } from "./api";
import type { ListenMode, Me, Role } from "./types";
import { applyAccentHue } from "./accent";
import { setCacheCapMb } from "./offline";

interface AuthState {
  ready: boolean; // initial /api/me resolved
  authenticated: boolean;
  role: Role | null;
  username: string | null;
  fullAccess: boolean;
  version: string;
  reviewCount: number;
  installPingAsk: boolean;
  dismissInstallPingAsk: () => void;
  // Loudness levelling, from /api/me — the player reads these to decide how far
  // to attenuate each track (see gainMultiplier in player.tsx).
  normalizePlayback: boolean;
  loudnessTargetLufs: number;
  // Offline preloading, from /api/me — how many upcoming queue tracks the
  // player's look-ahead caches for offline playback (0 = off).
  preloadAhead: number;
  // Kiosk Listen mode (admin setting, from /api/me) — the player-role shell
  // routes off this. Admin/guest always have the Listen page regardless.
  listenMode: ListenMode;
  login: (password: string, username?: string, totp?: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [role, setRole] = useState<Role | null>(null);
  const [username, setUsername] = useState<string | null>(null);
  const [fullAccess, setFullAccess] = useState(true);
  const [version, setVersion] = useState("");
  const [reviewCount, setReviewCount] = useState(0);
  const [installPingAsk, setInstallPingAsk] = useState(false);
  const [normalizePlayback, setNormalizePlayback] = useState(true);
  const [loudnessTargetLufs, setLoudnessTargetLufs] = useState(-14);
  const [preloadAhead, setPreloadAhead] = useState(5);
  const [listenMode, setListenMode] = useState<ListenMode>("off");

  const applyMe = useCallback((me: Me) => {
    setCsrfToken(me.csrf_token);
    setAuthenticated(me.authenticated);
    setRole(me.role ?? null);
    setUsername(me.username ?? null);
    // Absent (admin / older API) → full access; only an explicit false restricts.
    setFullAccess(me.full_access !== false);
    setVersion(me.version);
    setReviewCount(me.review_count || 0);
    setInstallPingAsk(Boolean(me.install_ping_ask));
    // Absent (older API) → keep the defaults rather than silently disabling.
    if (me.normalize_playback != null) setNormalizePlayback(me.normalize_playback);
    if (me.loudness_target_lufs != null) setLoudnessTargetLufs(me.loudness_target_lufs);
    if (me.preload_ahead != null) setPreloadAhead(me.preload_ahead);
    // The offline cache cap lives in the preload module (eviction runs there),
    // not in React state — nothing re-renders when it changes.
    if (me.preload_cache_mb != null) setCacheCapMb(me.preload_cache_mb);
    // Absent (older API) → "off", the safe kiosk default.
    setListenMode(me.listen_mode ?? "off");
    // Reconcile the per-account accent (set on another device) into this browser.
    // main.tsx already applied the localStorage value pre-mount to avoid a flash;
    // this overrides it once the server tells us the account's real preference.
    // null = no preference → keep whatever localStorage/default is showing.
    if (me.accent_hue != null) applyAccentHue(me.accent_hue);
  }, []);

  const dismissInstallPingAsk = useCallback(() => setInstallPingAsk(false), []);

  const refresh = useCallback(async () => {
    const me = await api.get<Me>("/api/me");
    applyMe(me);
  }, [applyMe]);

  useEffect(() => {
    refresh()
      .catch(() => setAuthenticated(false))
      .finally(() => setReady(true));
  }, [refresh]);

  // Any 401 from a protected endpoint means the session lapsed — flip to logged
  // out so the router shows the login screen rather than a wall of errors.
  useEffect(() => {
    setUnauthorizedHandler(() => setAuthenticated(false));
    return () => setUnauthorizedHandler(null);
  }, []);

  const login = useCallback(
    async (password: string, uname?: string, totp?: string) => {
      // username is optional: blank keeps the legacy admin/guest password-only flow;
      // a value logs in a named player user (Phase 5). totp is the admin's second
      // factor, sent only on the second step after a "totp_required" challenge.
      const body: { password: string; username?: string; totp?: string } = { password };
      if (uname && uname.trim()) body.username = uname.trim();
      if (totp && totp.trim()) body.totp = totp.trim();
      const res = await api.post<{ ok: boolean; csrf_token: string }>("/api/login", body);
      setCsrfToken(res.csrf_token);
      // Pull fresh /api/me for version + review count now that we're in.
      await refresh();
    },
    [refresh],
  );

  const logout = useCallback(async () => {
    // Tell the player (nested inside this provider — it can't be reached from
    // here directly) to stop before the session dies: signing out of the
    // installed PWA otherwise left the audio playing behind the login screen.
    // Fired only on explicit sign-out — a session *expiry* keeps the queue so
    // it can resume after signing back in.
    window.dispatchEvent(new Event("bpm:sign-out"));
    try {
      await api.post("/api/logout");
    } catch (e) {
      if (!(e instanceof ApiError)) throw e;
    }
    setAuthenticated(false);
    await refresh().catch(() => {});
  }, [refresh]);

  return (
    <AuthContext.Provider
      value={{ ready, authenticated, role, username, fullAccess, version, reviewCount, installPingAsk, dismissInstallPingAsk, normalizePlayback, loudnessTargetLufs, preloadAhead, listenMode, login, logout, refresh }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

/** Auth state when there is one, otherwise null — for consumers that must work
 *  outside an AuthProvider. The player uses this: it only wants the loudness
 *  settings, and hard-requiring the provider would make it unmountable on its
 *  own (which its tests do). */
export function useAuthOptional() {
  return useContext(AuthContext);
}
