import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import MiniPlayer from "../components/MiniPlayer";

// The Document Picture-in-Picture API isn't in TypeScript's DOM lib yet — the
// minimal surface we use. Chromium desktop only; feature-detected at runtime.
interface DocumentPiPOptions { width?: number; height?: number }
interface DocumentPiP extends EventTarget {
  readonly window: Window | null;
  requestWindow(options?: DocumentPiPOptions): Promise<Window>;
}
declare global {
  interface Window { documentPictureInPicture?: DocumentPiP }
}

// Roomy enough for cover + title/artist + the BPM·lock pill on the top row, a
// seekable progress bar, and the transport + volume below; the user can resize.
const PIP_W = 380;
const PIP_H = 226;

/** Clone the app's stylesheets into the PiP document so it inherits the design
 *  system (tokens, fonts, keyframes). Same-origin sheets are copied rule-by-rule;
 *  anything unreadable (cross-origin) is re-linked by href. */
function copyStyles(target: Document) {
  for (const sheet of Array.from(document.styleSheets)) {
    try {
      const css = Array.from(sheet.cssRules).map((r) => r.cssText).join("");
      const style = target.createElement("style");
      style.textContent = css;
      target.head.appendChild(style);
    } catch {
      if (sheet.href) {
        const link = target.createElement("link");
        link.rel = "stylesheet";
        link.href = sheet.href;
        if (sheet.media.length) link.media = sheet.media.mediaText;
        target.head.appendChild(link);
      }
    }
  }
}

interface MiniPlayerCtx {
  supported: boolean;
  isOpen: boolean;
  open(): Promise<void>;
  close(): void;
  toggle(): void;
}

const Ctx = createContext<MiniPlayerCtx | null>(null);

/** Owns the Document Picture-in-Picture window and portals <MiniPlayer> into it.
 *  Rendered once, high in the tree (inside PlayerProvider), so the floating
 *  player persists across route changes and keeps reading live player state. */
export function MiniPlayerProvider({ children }: { children: ReactNode }) {
  const [pipWindow, setPipWindow] = useState<Window | null>(null);
  const supported = typeof window !== "undefined" && "documentPictureInPicture" in window;

  const open = useCallback(async () => {
    if (!supported || pipWindow) return;
    try {
      const w = await window.documentPictureInPicture!.requestWindow({ width: PIP_W, height: PIP_H });
      copyStyles(w.document);
      w.document.documentElement.dataset.theme = document.documentElement.dataset.theme ?? "dark";
      w.document.title = "BPM Tagger";
      const b = w.document.body.style;
      b.background = "var(--bg)";
      b.color = "var(--text)";
      b.overflow = "hidden";
      // Reset state when the user closes the PiP window from its own chrome.
      w.addEventListener("pagehide", () => setPipWindow(null), { once: true });
      setPipWindow(w);
      // Installed as a PWA, the floating player should stand alone — tuck the
      // main app window away. The web platform has no standard window.minimize(),
      // so this only fires where the runtime exposes one (some PWA/desktop
      // shells do) and is a harmless no-op in a plain browser tab.
      const standalone = window.matchMedia?.("(display-mode: standalone)").matches
        || (navigator as unknown as { standalone?: boolean }).standalone === true;
      if (standalone) (window as unknown as { minimize?: () => void }).minimize?.();
    } catch {
      // User dismissed the prompt, or another PiP request is in flight — no-op.
    }
  }, [supported, pipWindow]);

  const close = useCallback(() => {
    pipWindow?.close();
    setPipWindow(null);
  }, [pipWindow]);

  const toggle = useCallback(() => {
    if (pipWindow) close();
    else void open();
  }, [pipWindow, open, close]);

  // Mirror the app's light/dark theme into the PiP window while it's open.
  useEffect(() => {
    if (!pipWindow) return;
    const obs = new MutationObserver(() => {
      pipWindow.document.documentElement.dataset.theme = document.documentElement.dataset.theme ?? "dark";
    });
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, [pipWindow]);

  // Don't leave an orphaned floating window if the app unmounts.
  useEffect(() => () => pipWindow?.close(), [pipWindow]);

  return (
    <Ctx.Provider value={{ supported, isOpen: !!pipWindow, open, close, toggle }}>
      {children}
      {pipWindow && createPortal(<MiniPlayer />, pipWindow.document.body)}
    </Ctx.Provider>
  );
}

export function useMiniPlayer() {
  const c = useContext(Ctx);
  if (!c) throw new Error("useMiniPlayer must be used within MiniPlayerProvider");
  return c;
}
