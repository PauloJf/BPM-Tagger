import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, ApiError, setCsrfToken, setUnauthorizedHandler } from "./api";
import type { Me, Role } from "./types";

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
      value={{ ready, authenticated, role, username, fullAccess, version, reviewCount, installPingAsk, dismissInstallPingAsk, login, logout, refresh }}
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
