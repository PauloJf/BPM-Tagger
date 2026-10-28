import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

// Settings is grouped by intent. Render the real page (everything around it
// mocked, as in the ratings test) and check the structure: group order, which
// sections sit in which group, and that every jump link has a target.
// Stable references: the page runs effects keyed on what these hooks return, so a
// fresh object per render would re-run them forever.
const stable = vi.hoisted(() => ({ settings: {} as Record<string, unknown>, params: new URLSearchParams() }));

vi.mock("react-router-dom", () => ({
  useSearchParams: () => [stable.params, () => {}] as const,
}));
vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: () => {}, setQueryData: () => {} }),
  useMutation: () => ({ mutate: () => {}, isPending: false }),
  useQuery: ({ queryKey }: { queryKey: unknown[] }) => {
    const k = queryKey[0];
    if (k === "settings") return { data: { settings: stable.settings, env_locked: [] }, isLoading: false };
    if (k === "trash") return { data: { count: 0, bytes: 0 } };
    if (k === "deleted") return { data: { count: 0 } };
    if (k === "isrc-fill") return { data: { running: false, total: 0, done: 0, filled: 0, unresolved: [] } };
    if (k === "lyrics-fill") return { data: { running: false, total: 0, done: 0, filled: 0, not_found: 0 } };
    if (k === "loudness-fill") return { data: { running: false, total: 0, done: 0, measured: 0, tagged: 0, failed: 0, remaining: 0 } };
    if (k === "waveform-fill") return { data: { running: false, total: 0, done: 0, filled: 0, failed: 0, remaining: 0 } };
    return { data: undefined, isLoading: false };
  },
}));
// The ratings section fetches a pick preview and reads .counts/.share off it, so a
// bare {} here would crash the page the moment the response lands.
vi.mock("../lib/api", () => ({
  api: {
    get: vi.fn((path: string) => Promise.resolve(
      path.startsWith("/api/settings/pick-preview")
        ? { counts: { "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, unrated: 10, new: 2 },
            share: { "1": 0.02, "2": 0.05, "3": 0.1, "4": 0.3, "5": 0.4, unrated: 0.1, new: 0.03 } }
        : {})),
    post: vi.fn(() => Promise.resolve({ ok: true })),
  },
}));
vi.mock("../lib/auth", () => ({ useAuth: () => ({ version: "1.0.0" }) }));
vi.mock("../components/Toggle", () => ({
  Toggle: ({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) => (
    <button type="button" aria-pressed={on} onClick={() => onChange(!on)}>{label}</button>
  ),
}));
vi.mock("../components/PlayerUsers", () => ({ default: () => null }));
vi.mock("../components/SubsonicSettings", () => ({ default: () => null }));
vi.mock("../hooks/useTitle", () => ({ useTitle: () => {} }));
vi.mock("../components/PageHeader", () => ({ default: () => null }));
vi.mock("../lib/accent", () => ({
  ACCENT_PRESETS: [{ name: "Violet", hue: 290 }],
  DEFAULT_ACCENT_HUE: 290,
  accentSwatch: () => "oklch(0.6 0.1 290)",
  applyAccentHue: () => {},
  initialAccentHue: () => 290,
}));
vi.mock("../lib/offline", () => ({
  cacheStats: () => ({ tracks: 0, bytes: 0 }),
  clearOffline: () => Promise.resolve(),
  offlineSupported: () => false,
  reconcileIndex: () => Promise.resolve(),
}));
vi.mock("../lib/version", () => ({ updateState: () => "up-to-date" }));

import Settings from "./Settings";

/** The page's headings in reading order: each group title, then the section cards
 *  under it, as ["group", "Run & Player"] / ["card", "Playback"] pairs. */
function outline(): Array<[string, string]> {
  const nodes = document.querySelectorAll(".settings-group-title, .settings-card");
  return [...nodes].map((n) => n.classList.contains("settings-group-title")
    ? ["group", n.textContent ?? ""]
    : ["card", n.querySelector("h2")?.textContent ?? ""]);
}

const groups = () => outline().filter(([k]) => k === "group").map(([, t]) => t);
const cardsUnder = (group: string) => {
  const o = outline();
  const from = o.findIndex(([k, t]) => k === "group" && t === group);
  const rest = o.slice(from + 1);
  const next = rest.findIndex(([k]) => k === "group");
  return (next < 0 ? rest : rest.slice(0, next)).map(([, t]) => t);
};

// The first render of this large page is slow when the whole suite runs in
// parallel; wait for it properly rather than for testing-library's default 1s.
vi.setConfig({ testTimeout: 20_000 });
const ready = () => screen.findByRole("heading", { name: "Playback" }, { timeout: 15_000 });

beforeEach(() => cleanup());

describe("Settings — grouped by intent", () => {
  it("orders the groups Run & Player, Library & integrations, Security, Maintenance, Appearance", async () => {
    render(<Settings />);
    await ready();
    expect(groups()).toEqual(["Run & Player", "Library & integrations", "Security", "Maintenance", "Appearance"]);
  });

  it("puts each section under the group it belongs to, in a sensible order", async () => {
    render(<Settings />);
    await ready();
    expect(cardsUnder("Run & Player")).toEqual(["Playback", "Run Mode", "Ratings & picking", "Player access"]);
    expect(cardsUnder("Library & integrations")).toEqual([
      "Library", "Navidrome Integration", "Subsonic API", "Notifications (ntfy)", "Artwork", "Lyrics", "Fill missing ISRCs"]);
    expect(cardsUnder("Security")).toEqual(["Admin sign-in", "Two-factor authentication", "Login protection"]);
    expect(cardsUnder("Maintenance")).toEqual(["Scan Behavior", "Operating Mode", "Trash", "Deleted Tracks", "Version", "Restart"]);
    expect(cardsUnder("Appearance")).toEqual(["Appearance"]);
  });

  it("keeps every section exactly once (none dropped, none duplicated)", async () => {
    render(<Settings />);
    await ready();
    const ids = [...document.querySelectorAll(".settings-card")].map((n) => n.id);
    expect(ids).toHaveLength(21);
    expect(new Set(ids).size).toBe(21);
  });

  it("gives the jump list a heading per group, in the same order", async () => {
    render(<Settings />);
    await ready();
    const titles = [...document.querySelectorAll(".settings-sidebar-title")].map((a) => a.textContent);
    expect(titles).toEqual(["Run & Player", "Library & integrations", "Security", "Maintenance", "Appearance"]);
  });

  it("points every jump link at something on the page", async () => {
    render(<Settings />);
    await ready();
    const links = [...document.querySelectorAll(".settings-sidebar a")];
    expect(links.length).toBeGreaterThan(21);
    for (const a of links) {
      const id = (a.getAttribute("href") ?? "").slice(1);
      expect(document.getElementById(id), `#${id}`).toBeTruthy();
    }
  });

  it("keeps the anchor the Run page deep-links to", async () => {
    render(<Settings />);
    await ready();
    expect(document.getElementById("sec-run")?.querySelector("h2")?.textContent).toBe("Run Mode");
  });
});
