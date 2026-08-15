import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";

// The card fetches its own pages; drive it through a controlled api.get.
const h = vi.hoisted(() => ({
  get: vi.fn(),
}));
vi.mock("../lib/api", () => ({ api: { get: h.get } }));

import RunJournal, { fmtRunDur, fmtRunWhen, type RunRow } from "./RunJournal";

const row = (over: Partial<RunRow> = {}): RunRow => ({
  id: 1,
  owner: "admin",
  owner_label: "Admin",
  started_at: "2026-03-12T07:40:00+00:00",
  ended_at: "2026-03-12T08:25:00+00:00",
  open: false,
  duration_ms: 2_700_000,     // 45m
  played_ms: 2_640_000,
  source: "library",
  source_label: "Library",
  target_bpm: 160,
  tracks: 12,
  avg_cadence: 158.6,
  stretched_pct: 63,
  ...over,
});

const page = (items: RunRow[], has_more = false) => ({ items, has_more });

beforeEach(() => {
  cleanup();
  h.get.mockReset();
});

describe("RunJournal — formatting helpers", () => {
  it("renders durations compactly", () => {
    expect(fmtRunDur(2_700_000)).toBe("45m");
    expect(fmtRunDur(5_000_000)).toBe("1h 23m");
    expect(fmtRunDur(45_000)).toBe("45s");
    expect(fmtRunDur(0)).toBe("0s");
  });

  it("renders a missing or unparseable start as a dash", () => {
    expect(fmtRunWhen(null)).toBe("—");
    expect(fmtRunWhen("not a date")).toBe("—");
    expect(fmtRunWhen("2026-03-12T07:40:00Z")).toMatch(/\d/);
  });
});

describe("RunJournal — rows", () => {
  it("shows a run's who / duration / source / numbers", async () => {
    h.get.mockResolvedValue(page([row()]));
    render(<RunJournal />);

    expect(await screen.findByText("Admin")).toBeTruthy();
    expect(screen.getByText("45m")).toBeTruthy();
    expect(screen.getByText("Library")).toBeTruthy();
    expect(screen.getByText("12")).toBeTruthy();
    expect(screen.getByText("159")).toBeTruthy();     // avg cadence, rounded
    expect(screen.getByText("63%")).toBeTruthy();
  });

  it("labels every owner kind and names a playlist source", async () => {
    h.get.mockResolvedValue(page([
      row({ id: 1, owner: "guest", owner_label: "Guest" }),
      row({ id: 2, owner: "player:3", owner_label: "runner",
            source: "playlist:7", source_label: "Tempo 160" }),
    ]));
    render(<RunJournal />);

    expect(await screen.findByText("Guest")).toBeTruthy();
    expect(screen.getByText("runner")).toBeTruthy();
    expect(screen.getByText("Tempo 160")).toBeTruthy();
  });

  it("marks a run that is still going", async () => {
    h.get.mockResolvedValue(page([row({ open: true, ended_at: null })]));
    render(<RunJournal />);
    expect(await screen.findByText("· live")).toBeTruthy();
  });

  it("shows a dash when no cadence was recorded", async () => {
    h.get.mockResolvedValue(page([row({ avg_cadence: null, played_ms: 0 })]));
    render(<RunJournal />);
    expect(await screen.findByText("—")).toBeTruthy();
  });

  it("invites a first run when there is nothing to show", async () => {
    h.get.mockResolvedValue(page([]));
    render(<RunJournal />);
    expect(await screen.findByText(/No runs recorded yet/)).toBeTruthy();
  });

  it("reports a failed load instead of an empty journal", async () => {
    h.get.mockRejectedValue(new Error("boom"));
    render(<RunJournal />);
    expect(await screen.findByText(/Failed to load the run journal/)).toBeTruthy();
  });
});

describe("RunJournal — paging", () => {
  it("appends the next page on Show more and drops the button at the end", async () => {
    h.get.mockResolvedValueOnce(page([row({ id: 1, owner_label: "Admin" })], true))
         .mockResolvedValueOnce(page([row({ id: 2, owner_label: "Guest" })], false));
    render(<RunJournal />);

    const btn = await screen.findByRole("button", { name: "Show more" });
    fireEvent.click(btn);

    await waitFor(() => expect(screen.getByText("Guest")).toBeTruthy());
    expect(screen.getByText("Admin")).toBeTruthy();       // page 1 kept
    // The second request asked for the rows after the ones already shown.
    expect(h.get.mock.calls[1][0]).toBe("/api/stats/runs?offset=1");
    expect(screen.queryByRole("button", { name: "Show more" })).toBeNull();
  });

  it("drops a row the next page repeats", async () => {
    // The list is newest-first, so a run started (or opened) between the two
    // requests slides the window down and page 2 hands back a row already on
    // screen. Rendering it twice would collide on React's key.
    h.get.mockResolvedValueOnce(page([row({ id: 1, owner_label: "One" }),
                                      row({ id: 2, owner_label: "Two" })], true))
         .mockResolvedValueOnce(page([row({ id: 2, owner_label: "Two" }),
                                      row({ id: 3, owner_label: "Three" })], false));
    const { container } = render(<RunJournal />);

    fireEvent.click(await screen.findByRole("button", { name: "Show more" }));
    await waitFor(() => expect(screen.getByText("Three")).toBeTruthy());
    expect(container.querySelectorAll("tbody tr")).toHaveLength(3);
    expect(screen.getAllByText("Two")).toHaveLength(1);
  });

  it("pages on rows received, so a page of pure repeats still moves on", async () => {
    h.get.mockResolvedValueOnce(page([row({ id: 1 }), row({ id: 2 })], true))
         .mockResolvedValueOnce(page([row({ id: 1 }), row({ id: 2 })], true))
         .mockResolvedValueOnce(page([row({ id: 3, owner_label: "Three" })], false));
    render(<RunJournal />);

    fireEvent.click(await screen.findByRole("button", { name: "Show more" }));
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    fireEvent.click(await screen.findByRole("button", { name: "Show more" }));
    await waitFor(() => expect(screen.getByText("Three")).toBeTruthy());
    // Offsets follow what the server handed over, not what survived the dedupe.
    expect(h.get.mock.calls.map((c) => c[0])).toEqual([
      "/api/stats/runs?offset=0", "/api/stats/runs?offset=2", "/api/stats/runs?offset=4"]);
  });
});

describe("RunJournal — owner scope", () => {
  it("filters server-side for a concrete account", async () => {
    h.get.mockResolvedValue(page([row({ owner: "player:3", owner_label: "runner" })]));
    render(<RunJournal owner="player:3" />);

    await screen.findByText("runner");
    expect(h.get).toHaveBeenCalledWith("/api/stats/runs?offset=0&owner=player%3A3");
  });

  it("never filters for the All scope", async () => {
    h.get.mockResolvedValue(page([row()]));
    render(<RunJournal owner="all" />);

    await screen.findByText("Admin");
    expect(h.get).toHaveBeenCalledWith("/api/stats/runs?offset=0");
  });

  it("explains that pre-attribution history has no runs, without asking the server", async () => {
    render(<RunJournal owner="unattributed" />);
    expect(await screen.findByText(/no per-run detail/)).toBeTruthy();
    expect(h.get).not.toHaveBeenCalled();
  });
});
