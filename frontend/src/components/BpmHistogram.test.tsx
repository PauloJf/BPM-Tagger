import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

import BpmHistogram from "./BpmHistogram";

beforeEach(cleanup);

const bars = (c: HTMLElement) => [...c.querySelectorAll(".hist-bar")] as HTMLElement[];
const labels = (c: HTMLElement) =>
  [...(c.querySelector(".hist-x-labels")?.children ?? [])].map((n) => n.textContent);

describe("BpmHistogram", () => {
  it("fills the empty buckets between the data's ends", () => {
    // The backend only returns buckets that hold tracks; drawn as-is, these two
    // would touch and the peak would land in the middle of the axis.
    const { container } = render(
      <BpmHistogram dist={[{ bpm: 100, count: 3 }, { bpm: 140, count: 9 }]} />);
    const drawn = bars(container);
    expect(drawn).toHaveLength(9);                      // 100…140 in 5-BPM steps
    expect(drawn[0].style.height).toBe("33.33333333333333%");
    expect(drawn[8].style.height).toBe("100%");         // the peak, at the far end
    expect(drawn.filter((b) => b.style.height === "0%")).toHaveLength(7);
  });

  it("labels the axis with the range it actually drew", () => {
    const { container } = render(
      <BpmHistogram dist={[{ bpm: 150, count: 2 }, { bpm: 170, count: 5 }]} />);
    // 150 to 175 (the top of the last bucket) — not a fixed 60–200 scale.
    expect(labels(container)).toEqual(["150", "156", "163", "169", "175"]);
  });

  it("gives the mini variant fewer ticks", () => {
    const { container } = render(
      <BpmHistogram dist={[{ bpm: 60, count: 1 }, { bpm: 200, count: 1 }]} mini />);
    expect(labels(container)).toEqual(["60", "133", "205"]);
  });

  it("says so, with no axis, when there is nothing to draw", () => {
    const { container } = render(<BpmHistogram dist={[]} emptyText="No BPMs yet." />);
    expect(screen.getByText("No BPMs yet.")).toBeTruthy();
    expect(bars(container)).toHaveLength(0);
    expect(container.querySelector(".hist-x-labels")).toBeNull();
  });

  it("positions the median rule within the drawn range", () => {
    const { container } = render(
      <BpmHistogram dist={[{ bpm: 150, count: 1 }, { bpm: 170, count: 1 }]} median={162.5} />);
    const rule = container.querySelector<HTMLElement>('[title^="Median"]');
    expect(rule?.style.left).toBe("50%");               // halfway across 150–175
  });
});
