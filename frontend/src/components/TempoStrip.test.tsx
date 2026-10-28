import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

// The strip reads two slices of shared queries; mock each so it renders alone.
const h = vi.hoisted(() => ({
  dist: undefined as unknown,
  presets: undefined as unknown,
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: ({ queryKey }: { queryKey: unknown[] }) => {
    if (queryKey[0] === "stats") return { data: h.dist };
    if (queryKey[0] === "run-readiness") return { data: h.presets };
    return { data: undefined };
  },
}));
vi.mock("../lib/api", () => ({ api: { get: vi.fn() } }));

import TempoStrip, { tempoParams, windowReach } from "./TempoStrip";

describe("windowReach — the shared 'what fits' rule", () => {
  const w = { target: 155, tol: 5, cadence: false };
  it("is inside for any overlap with the window, outside otherwise", () => {
    expect(windowReach(150, 155, w)).toBe(1);
    expect(windowReach(158, 165, w)).toBe(1);
    expect(windowReach(160, 165, w)).toBe(0); // touches the edge only
    expect(windowReach(100, 110, w)).toBe(0);
  });
  it("reaches half and double time only when asked", () => {
    expect(windowReach(75, 80, w)).toBe(0);
    expect(windowReach(75, 80, { ...w, cadence: true })).toBe(2);
    expect(windowReach(300, 320, { ...w, cadence: true })).toBe(2);
  });
  it("counts everything as inside when no tempo is set", () => {
    expect(windowReach(100, 110, { target: 0, tol: 5, cadence: false })).toBe(1);
  });
});

describe("tempoParams — the tempo filter in the URL", () => {
  it("sets the target and tolerance, and goes back to page one", () => {
    const next = tempoParams(new URLSearchParams("page=4&q=abba"), { bpm: "155", tol: "7" });
    expect(next.get("bpm")).toBe("155");
    expect(next.get("bpm_tol")).toBe("7");
    expect(next.get("page")).toBe("1");
    expect(next.get("q")).toBe("abba");
  });
  it("doesn't invent a page param for views that have no paging", () => {
    expect(tempoParams(new URLSearchParams(), { bpm: "155", tol: "5" }).has("page")).toBe(false);
  });
  it("defaults a blank tolerance to 5", () => {
    expect(tempoParams(new URLSearchParams(), { bpm: "120", tol: " " }).get("bpm_tol")).toBe("5");
  });
  it("clears the tempo, leaving the half/double switch alone", () => {
    const next = tempoParams(new URLSearchParams("bpm=155&bpm_tol=5&bpm_cadence=1"), { bpm: "", tol: "5" });
    expect(next.has("bpm")).toBe(false);
    expect(next.has("bpm_tol")).toBe(false);
    expect(next.get("bpm_cadence")).toBe("1");
  });
});

const DIST = [{ bpm: 75, count: 4 }, { bpm: 150, count: 10 }, { bpm: 155, count: 20 }, { bpm: 160, count: 6 }];
const PRESETS = [{ name: "Easy", bpm: 155 }, { name: "Steady", bpm: 165 }];

function setup(over: Partial<React.ComponentProps<typeof TempoStrip>> = {}) {
  const onApply = vi.fn();
  const onCadence = vi.fn();
  render(<TempoStrip bpm="" tol="5" cadence={false} total={undefined} onApply={onApply} onCadence={onCadence} {...over} />);
  return { onApply, onCadence };
}

const bar = (label: RegExp) => screen.getByRole("button", { name: label });

beforeEach(() => {
  cleanup();
  h.dist = DIST;
  h.presets = PRESETS;
});

describe("TempoStrip — readout", () => {
  it("reads Any tempo with no count when nothing is filtered", () => {
    setup({ total: 1455 });
    expect(screen.getByText("Any tempo")).toBeTruthy();
    expect(screen.queryByText(/fit$/)).toBeNull();
  });

  it("shows the window and how many tracks fit", () => {
    setup({ bpm: "155", tol: "5", total: 214 });
    expect(screen.getByText("150–160 BPM")).toBeTruthy();
    expect(screen.getByText("214 tracks fit")).toBeTruthy();
  });

  it("uses the singular for one track", () => {
    setup({ bpm: "155", total: 1 });
    expect(screen.getByText("1 track fit")).toBeTruthy();
  });
});

describe("TempoStrip — the histogram is the control", () => {
  it("centres the window on the bar that was clicked", () => {
    const { onApply } = setup({ bpm: "120", tol: "5" });
    fireEvent.click(bar(/^155 to 160 BPM/));
    expect(onApply).toHaveBeenCalledWith({ bpm: "158", tol: "5" });
  });

  it("lights the bars the window covers and dims the rest", () => {
    setup({ bpm: "155", tol: "5" });
    expect(bar(/^150 to 155/).getAttribute("data-reach")).toBe("1");
    expect(bar(/^155 to 160/).getAttribute("data-reach")).toBe("1");
    expect(bar(/^75 to 80/).getAttribute("data-reach")).toBe("0");
  });

  it("marks half-time bars as a softer hit, only when half/double time is on", () => {
    setup({ bpm: "155", tol: "5", cadence: true });
    expect(bar(/^75 to 80/).getAttribute("data-reach")).toBe("2");
  });

  it("describes each bar for assistive tech", () => {
    setup();
    expect(bar(/^155 to 160 BPM, 20 tracks$/)).toBeTruthy();
  });

  it("draws no bars when the library has no distribution yet", () => {
    h.dist = undefined;
    setup();
    expect(screen.queryByRole("group", { name: "Library BPM distribution" })).toBeNull();
  });
});

describe("TempoStrip — presets and the half/double switch", () => {
  it("applies a preset's cadence, keeping the tolerance", () => {
    const { onApply } = setup({ tol: "7" });
    fireEvent.click(screen.getByRole("button", { name: /Steady/ }));
    expect(onApply).toHaveBeenCalledWith({ bpm: "165", tol: "7" });
  });

  it("shows the matching preset as pressed", () => {
    setup({ bpm: "155" });
    expect(screen.getByRole("button", { name: /Easy/ }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: /Steady/ }).getAttribute("aria-pressed")).toBe("false");
  });

  it("Any clears the tempo", () => {
    const { onApply } = setup({ bpm: "155" });
    fireEvent.click(screen.getByRole("button", { name: "Any" }));
    expect(onApply).toHaveBeenCalledWith({ bpm: "", tol: "5" });
  });

  it("keeps half/double time unavailable until a tempo is set", () => {
    setup();
    expect((screen.getByRole("checkbox") as HTMLInputElement).disabled).toBe(true);
  });

  it("reports the switch", () => {
    const { onCadence } = setup({ bpm: "155" });
    fireEvent.click(screen.getByRole("checkbox"));
    expect(onCadence).toHaveBeenCalledWith(true);
  });
});
