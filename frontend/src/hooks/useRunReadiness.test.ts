import { describe, it, expect } from "vitest";
import { readyCount, type Readiness } from "./useRunReadiness";

const R: Readiness = {
  presets: [{ name: "Easy", bpm: 155 }],
  stretch_limit_pct: 15,
  library: { "155": 214 },
  playlists: [{ id: 7, name: "Long Runs", counts: { "155": 9 } }],
};

describe("readyCount", () => {
  it("reads the library when no playlist is given", () => {
    expect(readyCount(R, 155)).toBe(214);
  });
  it("reads one playlist when asked", () => {
    expect(readyCount(R, 155, 7)).toBe(9);
  });
  it("is 0 for a tempo nobody has", () => {
    expect(readyCount(R, 190)).toBe(0);
    expect(readyCount(R, 190, 7)).toBe(0);
  });
  it("is null, not 0, when it can't know", () => {
    expect(readyCount(undefined, 155)).toBeNull();
    expect(readyCount(R, 155, 99)).toBeNull();
  });
});
