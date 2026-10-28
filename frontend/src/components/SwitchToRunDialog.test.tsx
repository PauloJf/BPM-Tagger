import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";

// The player, the settings query and the queue endpoint are stood in for with
// hoisted holders, so each test drives exactly the queue and settings it needs.
const h = vi.hoisted(() => ({
  queue: [] as Array<{ path: string; title: string; bpm: number | null }>,
  current: null as null | { path: string; title: string; bpm: number | null },
  listenSource: null as number | "mine" | "library" | null,
  calls: [] as string[],
  playQueueArgs: null as null | [Array<{ path: string }>, number, unknown],
  runSource: undefined as unknown,
  lock: undefined as unknown,
  settings: {} as Record<string, unknown>,
  readiness: undefined as unknown,
  apiResult: { tracks: [] as Array<Record<string, unknown>> },
  apiError: null as null | Error,
  requested: [] as string[],
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({ data: { settings: h.settings } }),
}));
vi.mock("../hooks/useRunReadiness", () => ({
  useRunReadiness: () => ({ data: h.readiness }),
  readyCount: (r: { library: Record<string, number> } | undefined, bpm: number) => r ? r.library[String(bpm)] ?? 0 : null,
}));
vi.mock("../lib/api", () => ({
  api: {
    get: async (url: string) => { h.requested.push(url); if (h.apiError) throw h.apiError; return h.apiResult; },
  },
}));
vi.mock("../lib/player", async () => {
  const actual = await vi.importActual<typeof import("../lib/player")>("../lib/player");
  return {
    ...actual,
    usePlayer: () => ({
      orderedQueue: h.queue, current: h.current, listenSource: h.listenSource,
      playQueue: (t: Array<{ path: string }>, i: number, o: unknown) => { h.calls.push("playQueue"); h.playQueueArgs = [t, i, o]; },
      setRunSource: (s: unknown) => { h.calls.push("setRunSource"); h.runSource = s; },
      setTempoLock: (l: unknown) => { h.calls.push("setTempoLock"); h.lock = l; },
    }),
  };
});

import SwitchToRunDialog from "./SwitchToRunDialog";

const trk = (path: string, bpm: number | null) => ({ path, title: path, bpm });
const onCancel = vi.fn();
const onStarted = vi.fn();
const open = () => render(<SwitchToRunDialog onCancel={onCancel} onStarted={onStarted} />);
const radio = (re: RegExp) => screen.getByRole("radio", { name: re }) as HTMLInputElement;

beforeEach(() => {
  cleanup();
  localStorage.clear();
  onCancel.mockClear(); onStarted.mockClear();
  Object.assign(h, {
    queue: [trk("a", 155), trk("b", 150), trk("c", 100), trk("d", null)],
    current: trk("a", 155), listenSource: null, calls: [], playQueueArgs: null,
    runSource: undefined, lock: undefined, settings: {}, readiness: undefined,
    apiResult: { tracks: [{ path: "x", title: "x", bpm: 155 }, { path: "a", title: "a", bpm: 155 }, { path: "y", title: "y", bpm: 160 }] },
    apiError: null, requested: [],
  });
});

describe("SwitchToRunDialog — the choice", () => {
  it("offers both, with building a queue chosen the first time", () => {
    open();
    expect(radio(/Build a queue/).checked).toBe(true);
    expect(radio(/Keep this queue/).checked).toBe(false);
  });

  it("pre-selects the last answer", () => {
    localStorage.setItem("bpm.run.switchChoice", "stretch");
    open();
    expect(radio(/Keep this queue/).checked).toBe(true);
  });

  it("remembers the answer when Start is pressed", async () => {
    open();
    fireEvent.click(radio(/Keep this queue/));
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(onStarted).toHaveBeenCalled());
    expect(localStorage.getItem("bpm.run.switchChoice")).toBe("stretch");
  });

  it("cancels without touching the player", () => {
    open();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalled();
    expect(h.calls).toEqual([]);
  });
});

describe("SwitchToRunDialog — the count", () => {
  it("says how many queued tracks can reach the pace", () => {
    open();   // 155 default: a (155) and b (150) fit; c (100 → ×2 = 200, −22.5%) and d (no BPM) don't
    expect(screen.getByTestId("stretch-count").textContent).toContain("2 of 4 tracks can reach 155 BPM");
    expect(screen.getByTestId("stretch-count").textContent).toContain("The other 2");
  });

  it("changes live when another preset is picked", () => {
    open();
    fireEvent.click(screen.getByRole("button", { name: /Warmup · 120/ }));
    expect(screen.getByTestId("stretch-count").textContent).not.toContain("2 of 4 tracks can reach 155");
    expect(screen.getByTestId("stretch-count").textContent).toContain("120 BPM");
  });

  it("uses the stretch limit from settings", () => {
    h.settings = { run_stretch_limit_pct: 35 };
    open();   // ±35% lets c (100 → 200 is ×2; 155/100 = +55% direct, 155/200 = −22.5% folded) fit
    expect(screen.getByTestId("stretch-count").textContent).toContain("3 of 4");
  });

  it("says so plainly when everything fits", () => {
    h.queue = [trk("a", 155), trk("b", 150)];
    open();
    expect(screen.getByTestId("stretch-count").textContent).toContain("All 2 tracks can reach 155 BPM");
  });

  it("shows how many tracks are ready for a preset when the server says", () => {
    h.readiness = { library: { "155": 214 } };
    open();
    expect(screen.getByTestId("ready-count").textContent).toBe("214");
  });

  it("shows no ready count for a pace that isn't a preset", () => {
    h.readiness = { library: { "155": 214 } };
    localStorage.setItem("bpm.run.target", "158");
    open();
    expect(screen.queryByTestId("ready-count")).toBeNull();
    expect(screen.getByRole("button", { name: "158 BPM" })).toBeTruthy();
  });
});

describe("SwitchToRunDialog — Start with a rebuilt queue", () => {
  it("keeps the playing song first, adds the tracks that fit, then locks and scopes", async () => {
    open();
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(onStarted).toHaveBeenCalled());
    expect(h.requested).toEqual(["/api/run/queue?bpm=155"]);
    expect(h.playQueueArgs![0].map((t) => t.path)).toEqual(["a", "x", "y"]);   // "a" not doubled
    expect(h.playQueueArgs![1]).toBe(0);
    // ⚠ playQueue exits run mode, so the source and lock must come after it.
    expect(h.calls).toEqual(["playQueue", "setRunSource", "setTempoLock"]);
    expect(h.lock).toEqual({ target: 155, octave: true, stretchLimitPct: 15 });
  });

  it("builds for the pace picked in the dialog and remembers it", async () => {
    open();
    fireEvent.click(screen.getByRole("button", { name: /Tempo · 175/ }));
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(onStarted).toHaveBeenCalled());
    expect(h.requested).toEqual(["/api/run/queue?bpm=175"]);
    expect(localStorage.getItem("bpm.run.target")).toBe("175");
  });

  it("scopes the build to the remembered run source", async () => {
    localStorage.setItem("bpm.run.source", "pl:7");
    open();
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(onStarted).toHaveBeenCalled());
    expect(h.requested).toEqual(["/api/run/queue?bpm=155&playlist=7"]);
    expect(h.runSource).toBe(7);
  });

  it("stays open and changes nothing when no other track can reach the pace", async () => {
    h.apiResult = { tracks: [{ path: "a", title: "a", bpm: 155 }] };   // only the song already playing
    open();
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    expect((await screen.findByRole("alert")).textContent).toContain("No other tracks can reach 155 BPM");
    expect(h.calls).toEqual([]);
    expect(onStarted).not.toHaveBeenCalled();
  });

  it("reports a failed build and leaves the queue alone", async () => {
    h.apiError = new Error("server down");
    open();
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    expect((await screen.findByRole("alert")).textContent).toContain("server down");
    expect(h.calls).toEqual([]);
    expect(onStarted).not.toHaveBeenCalled();
  });
});

describe("SwitchToRunDialog — Start by stretching", () => {
  beforeEach(() => { localStorage.setItem("bpm.run.switchChoice", "stretch"); });

  it("locks the queue as it is, without asking the server for anything", async () => {
    open();
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(onStarted).toHaveBeenCalled());
    expect(h.requested).toEqual([]);
    expect(h.calls).toEqual(["setRunSource", "setTempoLock"]);   // never playQueue: the queue is kept
    expect(h.lock).toEqual({ target: 155, octave: true, stretchLimitPct: 15 });
  });

  it("keeps the run's refill inside the playlist the queue came from", async () => {
    h.listenSource = 12;
    open();
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(onStarted).toHaveBeenCalled());
    expect(h.runSource).toBe(12);
  });

  it("maps a whole-library Listen queue to the library", async () => {
    h.listenSource = "library";
    localStorage.setItem("bpm.run.source", "pl:7");
    open();
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(onStarted).toHaveBeenCalled());
    expect(h.runSource).toBeNull();
  });

  it("falls back to the remembered run source when the queue came from nowhere in particular", async () => {
    localStorage.setItem("bpm.run.source", "mine");
    open();
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(onStarted).toHaveBeenCalled());
    expect(h.runSource).toBe("mine");
  });
});
