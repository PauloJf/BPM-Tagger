import { describe, expect, it } from "vitest";
import { compareVersions, updateState } from "./version";

describe("compareVersions", () => {
  it("compares numerically, not lexically", () => {
    // The bug in issue #5: v2.7.0 read as "newer" than 2.17.0.
    expect(compareVersions("2.17.0", "2.7.0")).toBe(1);
    expect(compareVersions("2.7.0", "2.17.0")).toBe(-1);
    expect(compareVersions("2.17.0", "2.17.0")).toBe(0);
  });

  it("ignores a leading v and pads missing parts", () => {
    expect(compareVersions("v2.17.0", "2.17.0")).toBe(0);
    expect(compareVersions("2.17", "2.17.0")).toBe(0);
    expect(compareVersions("2.17.1", "2.17")).toBe(1);
  });

  it("returns null for unparseable input", () => {
    expect(compareVersions("unknown", "2.17.0")).toBeNull();
    expect(compareVersions("2.17.0", "")).toBeNull();
  });
});

describe("updateState", () => {
  it("flags an update only when the release is actually newer", () => {
    expect(updateState("2.17.0", "v2.18.0")).toBe("update-available");
    expect(updateState("2.17.0", "v2.17.0")).toBe("up-to-date");
    expect(updateState("2.17.0", "v2.7.0")).toBe("ahead");
  });

  it("is unknown without both versions", () => {
    expect(updateState(undefined, "v2.17.0")).toBe("unknown");
    expect(updateState("2.17.0", undefined)).toBe("unknown");
    expect(updateState("2.17.0", "unknown")).toBe("unknown");
  });
});
