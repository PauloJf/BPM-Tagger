import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "./lib/auth";
import { PlayerProvider } from "./lib/player";
import { MiniPlayerProvider } from "./lib/miniPlayer";
import ErrorBoundary from "./components/ErrorBoundary";
import App from "./App";
import { applyTheme, initialTheme } from "./lib/theme";
import { applyAccentHue, initialAccentHue } from "./lib/accent";
import "./index.css";

applyTheme(initialTheme());
applyAccentHue(initialAccentHue());

// PWA install support. Registered in prod only so the dev server never fights
// a stale worker; sw.js does no caching (see frontend/public/sw.js).
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false, refetchOnWindowFocus: false, staleTime: 5_000 },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <PlayerProvider>
            <MiniPlayerProvider>
              <ErrorBoundary>
                <App />
              </ErrorBoundary>
            </MiniPlayerProvider>
          </PlayerProvider>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
