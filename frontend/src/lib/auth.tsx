import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, ApiError, setCsrfToken } from "./api";
import type { Me } from "./types";

interface AuthState {
  ready: boolean; // initial /api/me resolved
  authenticated: boolean;
  version: string;
  reviewCount: number;
  login: (password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [version, setVersion] = useState("");
  const [reviewCount, setReviewCount] = useState(0);

  const applyMe = useCallback((me: Me) => {
    setCsrfToken(me.csrf_token);
    setAuthenticated(me.authenticated);
    setVersion(me.version);
    setReviewCount(me.review_count || 0);
  }, []);

  const refresh = useCallback(async () => {
    const me = await api.get<Me>("/api/me");
    applyMe(me);
  }, [applyMe]);

  useEffect(() => {
    refresh()
      .catch(() => setAuthenticated(false))
      .finally(() => setReady(true));
  }, [refresh]);

  const login = useCallback(
    async (password: string) => {
      const res = await api.post<{ ok: boolean; csrf_token: string }>("/api/login", { password });
      setCsrfToken(res.csrf_token);
      // Pull fresh /api/me for version + review count now that we're in.
      await refresh();
    },
    [refresh],
  );

  const logout = useCallback(async () => {
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
      value={{ ready, authenticated, version, reviewCount, login, logout, refresh }}
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
