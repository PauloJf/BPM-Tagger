import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

// The strip pulls in router + query context; mock them so the component renders
// against a payload the test controls.
const h = vi.hoisted(() => ({
  data: undefined as unknown,
}));

vi.mock("react-router-dom", () => ({
  Link: ({ to, children, ...rest }: { to: unknown; children?: unknown }) =>
    <a href={typeof to === "string" ? to : "#"} {...rest}>{children as never}</a>,
}));
vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({ data: h.data, isLoading: false }),
}));
vi.mock("../lib/api", () => ({ api: { get: vi.fn() } }));

import PlaylistStats, { fmtAge, fmtRuntime, type PlaylistStatsResponse } from "./PlaylistStats";

const payload = (over: Partial<PlaylistStatsResponse> = {},
                 matchedOver: Partial<PlaylistStatsResponse["matched"]> = {}): PlaylistStatsResponse => ({
  matched: {
    count: 3,
    runtime_ms: 11_520_000,          // 3 h 12 m
    analyzed: 2,
    bpm_distribution: [{ bpm: 120, count: 1 }, { bpm: 155, count: 1 }],
    plays_total: 42,
    top_played: [
      { path: "/music/a.mp3", title: "Alpha", artist: "Ann", play_count: 30 },
      { path: "/music/b.mp3", title: "Beta", artist: "Bo", play_count: 12 },
    ],
    ...matchedOver,
  },
  presets: [{ name: "Easy", bpm: 155 }, { name: "Steady", bpm: 165 }],
  runnable: { "155": 11, "165": 8 },
  stretch_limit_pct: 15,
  octave_fold: true,
  source: "spotify",
  last_synced_at: new Date().toISOString(),
  last_change_at: new Date().toISOString(),
  ...over,
});

beforeEach(() => {
  cleanup();
  h.data = payload();
});

describe("PlaylistStats — formatting helpers", () => {
  it("renders runtime as hours and minutes", () => {
    expect(fmtRuntime(11_520_000)).toBe("3 h 12 m");
    expect(fmtRuntime(720_000)).toBe("12 m");
    expect(fmtRuntime(3_600_000)).toBe("1 h 0 m");
    expect(fmtRuntime(0)).toBe("—");
  });

  it("renders staleness as a coarse age", () => {
    const days = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString();
    expect(fmtAge(days(0))).toBe("today");
    expect(fmtAge(days(1))).toBe("yesterday");
    expect(fmtAge(days(5))).toBe("5 days ago");
    expect(fmtAge(null)).toBe("unknown");
    expect(fmtAge("not-a-date")).toBe("unknown");
    // Anything older than a month falls back to a plain date.
    expect(fmtAge(days(90))).toMatch(/\d/);
  });
});

describe("PlaylistStats — the strip", () => {
  it("summarizes runtime, matched count, plays and the top three", () => {
    render(<PlaylistStats playlistId="1" />);
    expect(screen.getByText("3 h 12 m")).toBeTruthy();
    expect(screen.getByText(/across the 3 matched tracks/)).toBeTruthy();
    expect(screen.getByText("42")).toBeTruthy();
    expect(screen.getByText(/1\. Alpha · 30/)).toBeTruthy();
    expect(screen.getByText(/2\. Beta · 12/)).toBeTruthy();
  });

  it("links each top track to its track page", () => {
    const { container } = render(<PlaylistStats playlistId="1" />);
    const hrefs = Array.from(container.querySelectorAll("a")).map((a) => a.getAttribute("href"));
    expect(hrefs).toContain("/track?path=%2Fmusic%2Fa.mp3");
  });

  it("says so rather than showing a zero when nothing has been played", () => {
    h.data = payload({}, { plays_total: 0, top_played: [] });
    render(<PlaylistStats playlistId="1" />);
    expect(screen.getByText("never played")).toBeTruthy();
  });

  it("shows the same per-preset counts the playlist cards do", () => {
    render(<PlaylistStats playlistId="1" />);
    expect(screen.getByText("155:11")).toBeTruthy();
    expect(screen.getByText("165:8")).toBeTruthy();
    // The headline number is the best cadence on offer.
    expect(screen.getByText("11")).toBeTruthy();
  });

  it("drops presets nothing is runnable at, and says so when none are", () => {
    h.data = payload({ runnable: { "155": 4, "165": 0 } });
    render(<PlaylistStats playlistId="1" />);
    expect(screen.getByText("155:4")).toBeTruthy();
    expect(screen.queryByText("165:0")).toBeNull();

    cleanup();
    h.data = payload({ runnable: { "155": 0, "165": 0 } });
    render(<PlaylistStats playlistId="1" />);
    expect(screen.getByText("no cadence match")).toBeTruthy();
  });

  it("renders the shared BPM histogram across the playlist's own BPM range", () => {
    const { container } = render(<PlaylistStats playlistId="1" />);
    // 120 and 155, with the empty 5-BPM steps between them filled in, so the two
    // bars sit where their BPM says rather than side by side.
    const bars = container.querySelectorAll(".hist-bar");
    expect(bars).toHaveLength(8);
    expect([...bars].filter((b) => (b as HTMLElement).style.height !== "0%")).toHaveLength(2);
    expect(screen.getByText("2 of 3 analyzed")).toBeTruthy();
  });

  it("labels a local playlist's staleness as membership, not sync", () => {
    h.data = payload({ source: "local", last_synced_at: null });
    render(<PlaylistStats playlistId="1" />);
    expect(screen.getByText("Last changed")).toBeTruthy();
    expect(screen.getByText("membership")).toBeTruthy();
  });

  it("falls back to the sync time when a synced playlist has no membership stamp", () => {
    const iso = new Date(Date.now() - 5 * 86_400_000).toISOString();
    h.data = payload({ source: "spotify", last_change_at: null, last_synced_at: iso });
    render(<PlaylistStats playlistId="1" />);
    expect(screen.getAllByText("5 days ago").length).toBeGreaterThan(0);
  });

  it("renders nothing at all before the fetch lands, or with nothing matched", () => {
    h.data = undefined;
    const { container } = render(<PlaylistStats playlistId="1" />);
    expect(container.querySelector("[data-testid='playlist-stats']")).toBeNull();

    cleanup();
    // The coverage chips already say "0 have"; a strip of em-dashes adds nothing.
    h.data = payload({}, { count: 0 });
    const second = render(<PlaylistStats playlistId="1" />);
    expect(second.container.querySelector("[data-testid='playlist-stats']")).toBeNull();
  });
});
