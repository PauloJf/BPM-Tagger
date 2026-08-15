// Remember the last in-app page so the next launch reopens it.
//
// A plain reload already stays put (the URL survives), so this covers the
// entries that *lose* your place instead:
//   • the installed PWA, which always launches at its start_url (/run);
//   • opening the bare origin ("/") in a browser, which used to hardcode
//     the library;
//   • a session expiry, where the login screen used to dump you on /tracks
//     (Login now returns to the page you were bounced from).
// Deep links always win: only those launcher entries are ever redirected — a
// typed/bookmarked /run in a browser tab, or a /run?bpm= link, stays /run.

const KEY = "bpm.lastPage";

// The URL the app booted at, captured before the router redirects anything.
const BOOT = typeof window === "undefined" ? "" : window.location.pathname + window.location.search;

/** Installed-PWA detection: standalone display mode (Android/desktop) or the
 *  legacy navigator.standalone flag (iOS home-screen apps). */
export function isStandalone(): boolean {
  if (typeof window === "undefined") return false;
  if (window.matchMedia?.("(display-mode: standalone)")?.matches) return true;
  return (navigator as Navigator & { standalone?: boolean }).standalone === true;
}

export function rememberPage(page: string) {
  try { localStorage.setItem(KEY, page); } catch { /* ignore */ }
}

/** The saved page, or null when there's nothing sane to restore. */
export function lastPage(): string | null {
  try {
    const v = localStorage.getItem(KEY) || "";
    // In-app absolute path only — never an external URL ("//host"), the login
    // screen, or the root (which is itself a launcher entry).
    if (!v.startsWith("/") || v.startsWith("//") || v === "/" || v.startsWith("/login")) return null;
    return v;
  } catch {
    return null;
  }
}

/** Pure decision for the PWA-launch restore: booting the installed app at its
 *  start_url (bare /run, no query) goes to the saved page instead. Anything
 *  else — a browser tab on /run, a ?bpm= deep link, saved == boot — stays put.
 *  (The "/" browser entry is handled by the router's index redirect, which can
 *  read lastPage() directly.) Exported for tests. */
export function restoreTarget(bootUrl: string, standalone: boolean, saved: string | null): string | null {
  if (!saved || saved === bootUrl) return null;
  if (standalone && bootUrl === "/run") return saved;
  return null;
}

// One-shot: the restore may only happen for the app load's original entry —
// never re-fire on later remounts of a shell (login/logout, role switches).
let consumed = false;

/** The page to restore for this boot, or null. `current` is the URL at call
 *  time — if the router (or the user) already navigated away from the boot
 *  URL, the entry wasn't a fresh launcher start and nothing is restored. */
export function consumeBootRestore(current: string): string | null {
  if (consumed) return null;
  consumed = true;
  if (current !== BOOT) return null;
  return restoreTarget(BOOT, isStandalone(), lastPage());
}
