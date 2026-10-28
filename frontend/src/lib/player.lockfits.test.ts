import { describe, it, expect } from "vitest";
import { lockFits, lockRate, type TempoLock } from "./player";

const lock = (target: number, over: Partial<TempoLock> = {}): TempoLock =>
  ({ target, octave: true, stretchLimitPct: 15, ...over });

describe("lockFits", () => {
  it("is true when the stretch needed is inside the limit", () => {
    expect(lockFits(150, lock(155))).toBe(true);     // +3%
    expect(lockFits(170, lock(155))).toBe(true);     // −9%
  });

  it("is false when the track is further off than the limit", () => {
    expect(lockFits(125, lock(155))).toBe(false);    // +24%
    // …and lockRate agrees: it clamps instead of reaching the target.
    expect(lockRate(125, lock(155))).toBeCloseTo(1.15);
  });

  it("folds by octave before judging, when folding is on", () => {
    expect(lockFits(78, lock(155))).toBe(true);      // ×2 → 156
    expect(lockFits(78, lock(155, { octave: false }))).toBe(false);
  });

  it("is false for a track with no BPM, which plays at native speed", () => {
    expect(lockFits(null, lock(155))).toBe(false);
    expect(lockFits(undefined, lock(155))).toBe(false);
    expect(lockFits(0, lock(155))).toBe(false);
  });

  it("counts a track exactly on the limit as fitting", () => {
    expect(lockFits(100, lock(115))).toBe(true);     // +15%
  });
});
