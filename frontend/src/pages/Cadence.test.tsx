import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

// Cadence pulls in router/query/auth/player context; mock them so the page
// renders in isolation and its playback actions can be driven directly.
const h = vi.hoisted(() => ({
  tracks: [] as unknown[],
  ready: {} as Record<string, unknown>,
  presets: [
    { name: "Warmup", bpm: 120 }, { name: "Easy", bpm: 155 },
    { name: "Steady", bpm: 165 }, { name: "Tempo", bpm: 175 },
  ] as unknown,
  role: "admin" as string | null,
  params: "bpm=165",
  setParams: [] as unknown[],
  readyKeys: [] as unknown[],      // records the query keys the page asked for
  played: [] as Array<{ tracks: unknown[]; shuffle?: boolean }>,
  enqueued: [] as unknown[][],
}));

vi.mock("react-router-dom", () => ({
  Link: ({ to, children, ...rest }: { to: unknown; children?: unknown }) =>
    <a href={typeof to === "string" ? to : "#"} {...rest}>{children as never}</a>,
  useSearchParams: () => [
    new URLSearchParams(h.params),
    (v: unknown) => { h.setParams.push(v); },
  ],
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: ({ queryKey }: { queryKey: unknown[] }) => {
    if (queryKey[0] === "settings")
      return { data: { settings: { run_presets: h.presets } }, isLoading: false, isError: false };
    h.readyKeys.push(queryKey);
    return { data: { ...h.ready, tracks: h.tracks }, isLoading: false, isError: false };
  },
  useQueryClient: () => ({ invalidateQueries: () => {} }),
  useMutation: () => ({ mutate: () => {}, isPending: false }),
}));

vi.mock("../lib/api", () => ({ api: { get: vi.fn(), post: vi.fn() } }));
vi.mock("../lib/auth", () => ({ useAuth: () => ({ role: h.role }) }));
vi.mock("../hooks/useTitle", () => ({ useTitle: () => {} }));
vi.mock("../components/PageHeader", () => ({ default: () => null }));
vi.mock("../components/AddToPlaylistMenu", () => ({
  default: ({ paths }: { paths?: string[] }) =>
    <button data-testid="save-cadence" data-paths={JSON.stringify(paths ?? null)}>Add all to playlist…</button>,
}));
vi.mock("../lib/player", () => ({
  usePlayer: () => ({
    playQueue: (tracks: unknown[], _i: number, opts?: { shuffle?: boolean }) =>
      h.played.push({ tracks, shuffle: opts?.shuffle }),
    enqueueMany: (tracks: unknown[]) => h.enqueued.push(tracks),
  }),
}));

import Cadence from "./Cadence";

const track = (over: Record<string, unknown> = {}) => ({
  path: "/music/a.mp3", title: "A", artist: "Ar", bpm: 160, run_bpm: 160,
  rate: 1.03, starred: false, play_count: null, loudness_lufs: null, ...over,
});

const btn = (name: RegExp) => screen.getByRole("button", { name }) as HTMLButtonElement;

beforeEach(() => {
  cleanup();
  localStorage.clear();
  h.tracks = [];
  h.ready = { target: 165, count: 0, octave_fold: true, stretch_limit_pct: 15 };
  h.presets = [
    { name: "Warmup", bpm: 120 }, { name: "Easy", bpm: 155 },
    { name: "Steady", bpm: 165 }, { name: "Tempo", bpm: 175 },
  ];
  h.role = "admin";
  h.params = "bpm=165";
  h.setParams = [];
  h.readyKeys = [];
  h.played = [];
  h.enqueued = [];
});

describe("Cadence — rows and run math", () => {
  it("renders a row per runnable track with its native → folded × rate", () => {
    h.tracks = [track({ bpm: 80, run_bpm: 160, rate: 1.03 })];
    h.ready = { ...h.ready, count: 1 };
    const { container } = render(<Cadence />);

    expect(container.querySelectorAll(".pl-track-row")).toHaveLength(1);
    expect(screen.getByText(/80 → 160 ×1\.03/)).toBeTruthy();
  });

  it("says so when nothing is runnable at this cadence", () => {
    render(<Cadence />);
    expect(screen.getByText(/Nothing runnable at 165 BPM/)).toBeTruthy();
    for (const name of [/^Play$/, /^Shuffle$/, /Add to queue/]) {
      expect(btn(name).disabled).toBe(true);
    }
  });
});

describe("Cadence — playback", () => {
  it("Play queues every visible track, carrying loudness for levelling", () => {
    h.tracks = [
      track({ path: "/music/a.mp3", title: "A", loudness_lufs: -8.5 }),
      track({ path: "/music/b.mp3", title: "B" }),
    ];
    render(<Cadence />);

    fireEvent.click(btn(/^Play$/));
    expect(h.played[0].tracks).toEqual([
      { path: "/music/a.mp3", title: "A", artist: "Ar", bpm: 160, loudnessLufs: -8.5 },
      { path: "/music/b.mp3", title: "B", artist: "Ar", bpm: 160, loudnessLufs: null },
    ]);
    expect(h.played[0].shuffle).toBe(false);
  });

  it("Shuffle passes { shuffle: true } and Add to queue appends in one batch", () => {
    h.tracks = [track(), track({ path: "/music/b.mp3" })];
    render(<Cadence />);

    fireEvent.click(btn(/^Shuffle$/));
    expect(h.played[0].shuffle).toBe(true);

    fireEvent.click(btn(/Add to queue/));
    expect(h.enqueued).toHaveLength(1);
    expect(h.enqueued[0]).toHaveLength(2);
  });
});

describe("Cadence — target selection", () => {
  it("switches target from a preset chip", () => {
    render(<Cadence />);
    fireEvent.click(screen.getByRole("button", { name: /Tempo/ }));
    expect(h.setParams[0]).toEqual({ bpm: "175" });
  });

  it("fetches for the target in the URL", () => {
    h.params = "bpm=175";
    render(<Cadence />);
    expect(h.readyKeys.some((k) => Array.isArray(k) && k[0] === "run-ready" && k[1] === 175)).toBe(true);
  });

  it("falls back to the second preset when ?bpm= is missing or out of range", () => {
    h.params = "";
    render(<Cadence />);
    expect(h.readyKeys.some((k) => Array.isArray(k) && k[1] === 155)).toBe(true);

    cleanup();
    h.readyKeys = [];
    h.params = "bpm=9999";
    render(<Cadence />);
    expect(h.readyKeys.some((k) => Array.isArray(k) && k[1] === 155)).toBe(true);
  });

  it("normalizes legacy bare-number presets", () => {
    h.presets = [120, 150, 160, 170];
    render(<Cadence />);
    expect(screen.getByRole("button", { name: /Warmup/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /170/ })).toBeTruthy();
  });

  it("links into Run at the same target", () => {
    render(<Cadence />);
    const link = screen.getByRole("link", { name: /Open in Run/ });
    expect(link.getAttribute("href")).toBe("/run?bpm=165");
  });
});

describe("Cadence — save to a playlist", () => {
  it("hands the visible paths to the playlist menu, for an admin", () => {
    h.tracks = [track({ path: "/music/a.mp3" }), track({ path: "/music/b.mp3" })];
    render(<Cadence />);
    expect(JSON.parse(screen.getByTestId("save-cadence").getAttribute("data-paths")!))
      .toEqual(["/music/a.mp3", "/music/b.mp3"]);
  });

  it("hides it from a non-admin — playlist management is admin-only", () => {
    h.role = "player";
    h.tracks = [track()];
    render(<Cadence />);
    expect(screen.queryByTestId("save-cadence")).toBeNull();
  });
});
