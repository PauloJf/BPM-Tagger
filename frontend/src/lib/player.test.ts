import { describe, it, expect } from "vitest";
import { rebufferHoldSeconds, upcomingPaths } from "./player";

describe("rebufferHoldSeconds — adaptive rebuffer hold grows with stalls", () => {
  it("grows the required buffer-ahead on each successive stall", () => {
    const a = rebufferHoldSeconds(0);
    const b = rebufferHoldSeconds(1);
    const c = rebufferHoldSeconds(2);
    expect(a).toBeLessThan(b);
    expect(b).toBeLessThan(c);
  });

  it("clamps to the last (largest) step and never returns undefined", () => {
    const last = rebufferHoldSeconds(3);
    expect(rebufferHoldSeconds(4)).toBe(last);
    expect(rebufferHoldSeconds(99)).toBe(last);
    expect(rebufferHoldSeconds(0)).toBeGreaterThan(0);
  });

  it("treats a negative count as the first step", () => {
    expect(rebufferHoldSeconds(-5)).toBe(rebufferHoldSeconds(0));
  });
});

describe("upcomingPaths — run-mode look-ahead window", () => {
  const q = [
    { path: "/a.mp3" },
    { path: "/b.mp3" },
    { path: "/c.mp3" },
    { path: "/d.mp3" },
  ];
  const order = [0, 1, 2, 3];

  it("returns the next N tracks after the current position", () => {
    expect(upcomingPaths(order, 0, q, "off", 2)).toEqual(["/b.mp3", "/c.mp3"]);
    expect(upcomingPaths(order, 1, q, "off", 2)).toEqual(["/c.mp3", "/d.mp3"]);
  });

  it("stops at the end of the queue when repeat is off", () => {
    expect(upcomingPaths(order, 3, q, "off", 2)).toEqual([]);
    expect(upcomingPaths(order, 2, q, "off", 2)).toEqual(["/d.mp3"]);
  });

  it("wraps to the front when repeat is all", () => {
    expect(upcomingPaths(order, 3, q, "all", 2)).toEqual(["/a.mp3", "/b.mp3"]);
  });

  it("respects the shuffle order (indices come from `order`)", () => {
    expect(upcomingPaths([2, 0, 3, 1], 0, q, "off", 2)).toEqual(["/a.mp3", "/d.mp3"]);
  });

  it("skips external/preview clips (own src / ephemeral) — nothing to prefetch", () => {
    const mixed = [
      { path: "/a.mp3" },
      { path: "preview:dz:1", ephemeral: true },
      { path: "/ext", src: "https://cdn/ext.mp3" },
      { path: "/d.mp3" },
    ];
    expect(upcomingPaths([0, 1, 2, 3], 0, mixed, "off", 3)).toEqual(["/d.mp3"]);
  });
});
