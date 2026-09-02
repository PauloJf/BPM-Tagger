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

// iOS 17+ standalone PWAs auto-zoom on input focus even with 16px fonts (the
// CSS layer in design-system.css). maximum-scale=1 suppresses only that
// auto-zoom on iOS — pinch gestures ignore it since iOS 10 — but Android
// Chrome would honor it and block pinch-zoom entirely, so it must be applied
// at runtime, gated to iOS, never in the static index.html.
const isIOS =
  /iPad|iPhone|iPod/.test(navigator.userAgent) ||
  (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1); // iPadOS masquerades as macOS
if (isIOS) {
  const meta = document.querySelector('meta[name="viewport"]');
  if (meta) meta.setAttribute("content", meta.getAttribute("content") + ", maximum-scale=1");
}

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
