import { describe, it, expect } from "vitest";
import { gainMultiplier } from "./player";

/** The multiplier is an amplitude scale, so a dB figure converts as 10^(dB/20). */
const db = (multiplier: number) => 20 * Math.log10(multiplier);

describe("gainMultiplier — loudness levelling for playback", () => {
  it("attenuates a track louder than the target by exactly the difference", () => {
    // 6 LU hotter than the target → 6 dB down.
    expect(db(gainMultiplier(-8, -14))).toBeCloseTo(-6, 5);
  });

  it("never boosts a quiet track", () => {
    // HTMLMediaElement.volume is clamped to [0,1]: there is no headroom above 1,
    // so a track below the target has to play as-is.
    expect(gainMultiplier(-20, -14)).toBe(1);
    expect(gainMultiplier(-40, -14)).toBe(1);
  });

  it("leaves a track already at the target untouched", () => {
    expect(gainMultiplier(-14, -14)).toBe(1);
  });

  it("plays unmeasured tracks at full volume", () => {
    expect(gainMultiplier(null, -14)).toBe(1);
    expect(gainMultiplier(undefined, -14)).toBe(1);
  });

  it("is a no-op when levelling is disabled", () => {
    expect(gainMultiplier(-4, -14, false)).toBe(1);
  });

  it("caps how far it will attenuate, so bad data can't mute a track", () => {
    const absurd = gainMultiplier(999, -14);
    expect(db(absurd)).toBeCloseTo(-20, 5);
    expect(absurd).toBeGreaterThan(0);
  });

  it("ignores a non-finite measurement", () => {
    expect(gainMultiplier(Number.NaN, -14)).toBe(1);
    expect(gainMultiplier(-Infinity, -14)).toBe(1);
  });

  it("follows the target: a lower target attenuates more", () => {
    const at14 = gainMultiplier(-6, -14);
    const at18 = gainMultiplier(-6, -18);
    expect(at18).toBeLessThan(at14);
    expect(db(at18) - db(at14)).toBeCloseTo(-4, 5);
  });

  it("levels two tracks of different loudness to the same output", () => {
    // The whole point: after levelling, a -6 and a -10 LUFS track differ by 0 dB
    // instead of 4.
    const hot = -6, mid = -10, target = -14;
    const hotOut = mid + db(gainMultiplier(mid, target));
    const midOut = hot + db(gainMultiplier(hot, target));
    expect(hotOut).toBeCloseTo(midOut, 5);
    expect(hotOut).toBeCloseTo(target, 5);
  });

  it("stays within [0,1] across a wide sweep of inputs", () => {
    for (let lufs = -60; lufs <= 10; lufs += 0.5) {
      const g = gainMultiplier(lufs, -14);
      expect(g).toBeGreaterThan(0);
      expect(g).toBeLessThanOrEqual(1);
    }
  });
});
