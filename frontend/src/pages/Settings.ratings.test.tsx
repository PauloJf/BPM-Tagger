import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";

// Settings pulls in a lot of infra (react-query, router, several hooks/modules).
// Mock all of it so the page renders in isolation, and drive the API calls the
// Ratings & picking section makes through a controlled api mock.
const h = vi.hoisted(() => ({
  settings: {} as Record<string, unknown>,
  posts: [] as Array<{ path: string; body: unknown }>,
  getCalls: [] as string[],
}));

vi.mock("react-router-dom", () => ({
  useSearchParams: () => {
    const params = new URLSearchParams();
    return [params, () => {}] as const;
  },
}));

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: () => {}, setQueryData: () => {} }),
  useMutation: () => ({ mutate: () => {}, isPending: false }),
  useQuery: ({ queryKey }: { queryKey: unknown[] }) => {
    const k = queryKey[0];
    if (k === "settings") return { data: { settings: h.settings, env_locked: [] }, isLoading: false };
    if (k === "trash") return { data: { count: 0, bytes: 0 } };
    if (k === "deleted") return { data: { count: 0 } };
    if (k === "isrc-fill") return { data: { running: false, total: 0, done: 0, filled: 0, unresolved: [] } };
    if (k === "lyrics-fill") return { data: { running: false, total: 0, done: 0, filled: 0, not_found: 0 } };
    if (k === "loudness-fill") return { data: { running: false, total: 0, done: 0, measured: 0, tagged: 0, failed: 0, remaining: 0 } };
    if (k === "waveform-fill") return { data: { running: false, total: 0, done: 0, filled: 0, failed: 0, remaining: 0 } };
    if (k === "grabber-status") return { data: { enabled: false } };
    return { data: undefined, isLoading: false };
  },
}));

vi.mock("../lib/api", () => ({
  api: {
    get: vi.fn((path: string) => {
      h.getCalls.push(path);
      if (path.startsWith("/api/settings/pick-preview")) {
        return Promise.resolve({
          counts: { "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, unrated: 10, new: 2 },
          share: { "1": 0.02, "2": 0.05, "3": 0.1, "4": 0.3, "5": 0.4, unrated: 0.1, new: 0.03 },
        });
      }
      return Promise.resolve({});
    }),
    post: vi.fn((path: string, body: unknown) => {
      h.posts.push({ path, body });
      return Promise.resolve({ ok: true });
    }),
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
import { api } from "../lib/api";

beforeEach(() => {
  cleanup();
  h.settings = {};
  h.posts = [];
  h.getCalls = [];
});

describe("Settings — Ratings & picking", () => {
  it("renders the section with the defaults and no more Run 'prefer' toggles", async () => {
    render(<Settings />);
    expect(await screen.findByRole("heading", { name: "Ratings & picking" })).toBeTruthy();
    expect(screen.queryByText("Prefer starred tracks")).toBeNull();
    expect(screen.queryByText("Prefer familiar tracks")).toBeNull();
  });

  it("saves the use-ratings toggle, weights and new-songs factor", async () => {
    render(<Settings />);
    await screen.findByRole("heading", { name: "Ratings & picking" });
    fireEvent.click(screen.getByRole("button", { name: "Save Ratings Settings" }));
    await waitFor(() => expect(h.posts.some((p) => p.path === "/api/settings/ratings")).toBe(true));
    const call = h.posts.find((p) => p.path === "/api/settings/ratings")!;
    expect(call.body).toEqual({
      pick_use_ratings: true,
      pick_weights: [0.1, 0.5, 1, 1, 3, 6],
      pick_new_factor: 1,
    });
  });

  it("fetches a live preview (debounced) when a weight changes", async () => {
    render(<Settings />);
    await screen.findByRole("heading", { name: "Ratings & picking" });
    fireEvent.change(screen.getByLabelText("5★ weight"), { target: { value: "8" } });
    await waitFor(() => expect(h.getCalls.some((c) => c.includes("weights=0.1%2C0.5%2C1%2C1%2C3%2C8"))).toBe(true));
    const call = [...h.getCalls].reverse().find((c) => c.startsWith("/api/settings/pick-preview"))!;
    expect(call).toContain("use_ratings=1");
    expect(call).toContain("weights=0.1%2C0.5%2C1%2C1%2C3%2C8");
    expect(call).toContain("new_factor=1");
    expect(await screen.findByText(/40\.0% · 5/)).toBeTruthy();
  });

  it("Reset to defaults restores the shipped weights", async () => {
    render(<Settings />);
    await screen.findByRole("heading", { name: "Ratings & picking" });
    fireEvent.change(screen.getByLabelText("5★ weight"), { target: { value: "8" } });
    fireEvent.click(screen.getByRole("button", { name: "Reset to defaults" }));
    expect((screen.getByLabelText("5★ weight") as HTMLInputElement).value).toBe("6");
  });
});
