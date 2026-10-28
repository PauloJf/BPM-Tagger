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

import TempoStrip from "./TempoStrip";

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
