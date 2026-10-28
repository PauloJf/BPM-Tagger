import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent, within } from "@testing-library/react";

// Home reads three things — run readiness, the stats summary and the scan state —
// so mock each in isolation and assert what the page does with them.
const h = vi.hoisted(() => ({
  readiness: undefined as unknown,
  readinessError: false,
  stats: undefined as unknown,
  reviewCount: 0,
  scan: { state: "idle", progress: null as unknown },
  act: vi.fn(),
  runs: undefined as unknown,
  toggle: vi.fn(),
  player: {
    current: null as null | { path: string; title: string; artist?: string; ephemeral?: boolean },
    playing: false,
    previewing: false,
    orderedQueue: [] as unknown[],
    orderPos: 0,
    tempoLock: null as null | { target: number },
  },
}));

vi.mock("react-router-dom", () => ({
  Link: ({ to, children, ...rest }: { to: unknown; children?: unknown }) =>
    <a href={typeof to === "string" ? to : "#"} {...rest}>{children as never}</a>,
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: ({ queryKey }: { queryKey: unknown[] }) => {
    if (queryKey[0] === "run-readiness")
      return { data: h.readiness, isLoading: false, isError: h.readinessError };
    if (queryKey[0] === "stats")
      return { data: h.stats, isLoading: false, isError: false };
    if (queryKey[0] === "runs")
      return { data: h.runs, isLoading: false, isError: false };
    return { data: undefined, isLoading: false, isError: false };
  },
}));

vi.mock("../lib/api", () => ({ api: { get: vi.fn() } }));
vi.mock("../lib/auth", () => ({ useAuth: () => ({ reviewCount: h.reviewCount }) }));
vi.mock("../hooks/useScan", () => ({
  useScan: () => ({ state: h.scan.state, progress: h.scan.progress, act: h.act }),
}));
vi.mock("../hooks/useTitle", () => ({ useTitle: () => {} }));
vi.mock("../lib/player", () => ({
  usePlayer: () => ({ ...h.player, toggle: h.toggle }),
}));

import Home from "./Home";

const READINESS = {
  presets: [{ name: "Easy", bpm: 155 }, { name: "Steady", bpm: 165 }, { name: "Tempo", bpm: 175 }],
  stretch_limit_pct: 15,
  library: { "155": 214, "165": 163, "175": 0 },
};

function statsWith(over: Record<string, number> = {}) {
  return { summary: { total: 1455, done: 1402, decode_problems: 0, ...over } };
}

beforeEach(() => {
  cleanup();
  h.readiness = READINESS;
  h.readinessError = false;
  h.stats = statsWith();
  h.reviewCount = 0;
  h.scan = { state: "idle", progress: null };
  h.act.mockClear();
  h.toggle.mockClear();
  h.runs = undefined;
  h.player = { current: null, playing: false, previewing: false, orderedQueue: [], orderPos: 0, tempoLock: null };
});

describe("Home — start a run", () => {
  it("shows one card per preset, each deep-linking Run at that cadence", () => {
    render(<Home />);
    const run = screen.getByRole("region", { name: "Start a run" });
    const easy = within(run).getByRole("link", { name: /Easy/ });
    expect(easy.getAttribute("href")).toBe("/run?bpm=155");
    expect(easy.textContent).toContain("155");
    expect(easy.textContent).toContain("214");
    expect(within(run).getByRole("link", { name: /Steady/ }).getAttribute("href")).toBe("/run?bpm=165");
    expect(within(run).getByRole("link", { name: /Tempo/ }).getAttribute("href")).toBe("/run?bpm=175");
  });

  it("offers a Custom card that opens Run without a target", () => {
    render(<Home />);
    expect(screen.getByRole("link", { name: /Custom/ }).getAttribute("href")).toBe("/run");
  });

  it("keeps a preset with nothing runnable visible, reading 0 ready", () => {
    render(<Home />);
    const tempo = screen.getByRole("link", { name: /Tempo/ });
    expect(tempo.textContent).toContain("0");
    expect(tempo.textContent).toContain("ready");
  });

  it("renders no cards while readiness is still loading, so nothing jumps in later", () => {
    h.readiness = undefined;
    render(<Home />);
    expect(screen.queryByRole("link", { name: /Easy/ })).toBeNull();
    expect(screen.queryByText(/Couldn't load/)).toBeNull();
  });

  it("points at Run when the presets can't be loaded", () => {
    h.readiness = undefined;
    h.readinessError = true;
    render(<Home />);
    expect(screen.getByText(/Couldn't load the run presets/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open Run" }).getAttribute("href")).toBe("/run");
  });
});

describe("Home — needs attention", () => {
  it("is absent when nothing needs attention", () => {
    render(<Home />);
    expect(screen.queryByRole("region", { name: "Needs attention" })).toBeNull();
  });

  it("links the review count to the Review page", () => {
    h.reviewCount = 6;
    render(<Home />);
    const attn = screen.getByRole("region", { name: "Needs attention" });
    const row = within(attn).getByRole("link", { name: /6 tracks need a BPM check/ });
    expect(row.getAttribute("href")).toBe("/review");
  });

  it("uses the singular for exactly one track", () => {
    h.reviewCount = 1;
    render(<Home />);
    expect(screen.getByText("1 track needs a BPM check")).toBeTruthy();
  });

  it("links decode problems to the library's problems filter", () => {
    h.stats = statsWith({ decode_problems: 2 });
    render(<Home />);
    const row = screen.getByRole("link", { name: /2 files had decode problems/ });
    expect(row.getAttribute("href")).toBe("/tracks?filter=problems");
  });

  it("mentions a running scan, with its progress", () => {
    h.reviewCount = 3;
    h.scan = { state: "analysing", progress: { completed: 40, total: 120 } };
    render(<Home />);
    expect(screen.getByText("Analyzing your library")).toBeTruthy();
    expect(screen.getByText("40 of 120 tracks")).toBeTruthy();
  });

  it("counts every row in the badge", () => {
    h.reviewCount = 6;
    h.stats = statsWith({ decode_problems: 2 });
    render(<Home />);
    const attn = screen.getByRole("region", { name: "Needs attention" });
    expect(within(attn).getByText("2")).toBeTruthy();
  });
});

describe("Home — first run", () => {
  it("replaces the cards with a scan call to action when nothing is analysed", () => {
    h.stats = statsWith({ done: 0, total: 0 });
    render(<Home />);
    expect(screen.getByText("Scan your library")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Start a run" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Start scan" }));
    expect(h.act).toHaveBeenCalledWith("start");
  });

  it("shows progress instead of the button while a first scan runs", () => {
    h.stats = statsWith({ done: 0, total: 0 });
    h.scan = { state: "analysing", progress: { completed: 3, total: 50 } };
    render(<Home />);
    expect(screen.queryByRole("button", { name: "Start scan" })).toBeNull();
    expect(screen.getByText(/3 of 50 tracks/)).toBeTruthy();
  });

  it("does not flash the call to action before the stats arrive", () => {
    h.stats = undefined;
    render(<Home />);
    expect(screen.queryByText("Scan your library")).toBeNull();
  });
});

describe("Home — resume", () => {
  const queue = (n: number) => Array.from({ length: n }, (_, i) => ({ path: `/m/${i}.mp3`, title: `T${i}` }));

  it("is absent when there is no queue", () => {
    render(<Home />);
    expect(screen.queryByRole("region", { name: "Resume" })).toBeNull();
  });

  it("offers to pick a run back up, naming the preset and the position", () => {
    h.player = { ...h.player, current: { path: "/m/1.mp3", title: "Don't Stop Me Now", artist: "Queen" },
      orderedQueue: queue(38), orderPos: 13, tempoLock: { target: 155 } };
    render(<Home />);
    const r = screen.getByRole("region", { name: "Resume" });
    expect(within(r).getByText("Don't Stop Me Now")).toBeTruthy();
    expect(r.textContent).toContain("Queen");
    expect(r.textContent).toContain("Easy 155");
    expect(r.textContent).toContain("track 14 of 38");
    expect(within(r).getByRole("link", { name: "Open Run" }).getAttribute("href")).toBe("/run");
  });

  it("falls back to the raw cadence for a target that is not a preset", () => {
    h.player = { ...h.player, current: { path: "/m/1.mp3", title: "A" }, orderedQueue: queue(5), tempoLock: { target: 142 } };
    render(<Home />);
    expect(screen.getByRole("region", { name: "Resume" }).textContent).toContain("142 steps/min");
  });

  it("points a plain Listen queue at Listen", () => {
    h.player = { ...h.player, current: { path: "/m/1.mp3", title: "A" }, orderedQueue: queue(5), tempoLock: null };
    render(<Home />);
    const r = screen.getByRole("region", { name: "Resume" });
    expect(r.textContent).toContain("Listening");
    expect(within(r).getByRole("link", { name: "Open Listen" }).getAttribute("href")).toBe("/listen");
  });

  it("Resume starts the player, and reads Pause while it is playing", () => {
    h.player = { ...h.player, current: { path: "/m/1.mp3", title: "A" }, orderedQueue: queue(5) };
    const { rerender } = render(<Home />);
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    expect(h.toggle).toHaveBeenCalledTimes(1);
    h.player = { ...h.player, playing: true };
    rerender(<Home />);
    expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
    expect(screen.getByText("Playing now")).toBeTruthy();
  });

  it("ignores a one-off preview clip", () => {
    h.player = { ...h.player, current: { path: "preview:dz:1", title: "Clip", ephemeral: true }, orderedQueue: queue(1) };
    render(<Home />);
    expect(screen.queryByRole("region", { name: "Resume" })).toBeNull();
  });

  it("is left out of the first-run screen", () => {
    h.stats = statsWith({ done: 0, total: 0 });
    h.player = { ...h.player, current: { path: "/m/1.mp3", title: "A" }, orderedQueue: queue(5) };
    render(<Home />);
    expect(screen.queryByRole("region", { name: "Resume" })).toBeNull();
  });
});

describe("Home — recent runs", () => {
  const run = (id: number, over: Record<string, unknown> = {}) => ({
    id, owner: "admin", owner_label: "Admin", started_at: "2026-10-27T07:12:00", ended_at: "2026-10-27T07:54:00",
    open: false, duration_ms: 42 * 60_000, played_ms: 40 * 60_000, source: "library", source_label: "Whole library",
    target_bpm: 155, tracks: 11, avg_cadence: 154.2, stretched_pct: 3, ...over,
  });

  it("is absent with no runs", () => {
    h.runs = { items: [], has_more: false };
    render(<Home />);
    expect(screen.queryByRole("region", { name: "Recent runs" })).toBeNull();
  });

  it("lists the last three runs with duration, cadence and source", () => {
    h.runs = { items: [run(1), run(2, { avg_cadence: 164.4, target_bpm: 165, source_label: "Long Runs" }), run(3), run(4)], has_more: true };
    render(<Home />);
    const r = screen.getByRole("region", { name: "Recent runs" });
    expect(within(r).getAllByRole("link", { name: /spm avg/ })).toHaveLength(3);
    expect(r.textContent).toContain("42m");
    expect(r.textContent).toContain("154");
    expect(r.textContent).toContain("164");
    expect(r.textContent).toContain("target 165");
    expect(r.textContent).toContain("Long Runs");
  });

  it("says Running now for an open run", () => {
    h.runs = { items: [run(1, { open: true })], has_more: false };
    render(<Home />);
    expect(screen.getByText("Running now")).toBeTruthy();
  });

  it("shows a dash when a run has no cadence yet", () => {
    h.runs = { items: [run(1, { avg_cadence: null })], has_more: false };
    render(<Home />);
    expect(screen.getByRole("region", { name: "Recent runs" }).textContent).toContain("—");
  });

  it("links through to the journal on Stats", () => {
    h.runs = { items: [run(1)], has_more: false };
    render(<Home />);
    expect(screen.getByRole("link", { name: "All runs" }).getAttribute("href")).toBe("/stats");
  });
});
