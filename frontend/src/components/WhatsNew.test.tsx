import { describe, it, expect } from "vitest";
import { parseChangelog, cmpVersion, sectionsSince } from "./Changelog";
import { decideWhatsNew } from "./WhatsNew";

const SAMPLE = `# Changelog

## v2.6.10 — 2026-07-18

- **New thing.** with \`code\` and [a link](https://example.com)
- Another

## v2.6.9 — 2026-07-17

- Older thing
`;

describe("changelog parsing", () => {
  it("splits into version sections with dates", () => {
    const s = parseChangelog(SAMPLE);
    expect(s.map((x) => x.version)).toEqual(["2.6.10", "2.6.9"]);
    expect(s[0].date).toBe("2026-07-18");
    expect(s[0].body).toContain("New thing");
    expect(s[1].body).toContain("Older thing");
  });

  it("compares versions numerically (10 > 9)", () => {
    expect(cmpVersion("2.6.10", "2.6.9")).toBeGreaterThan(0);
    expect(cmpVersion("2.6.9", "2.6.10")).toBeLessThan(0);
    expect(cmpVersion("2.6.10", "2.6.10")).toBe(0);
  });

  it("keeps only sections newer than `since`", () => {
    const s = sectionsSince(parseChangelog(SAMPLE), "2.6.9");
    expect(s.map((x) => x.version)).toEqual(["2.6.10"]);
  });

  it("returns all sections when `since` is unset", () => {
    expect(sectionsSince(parseChangelog(SAMPLE)).length).toBe(2);
  });
});

describe("decideWhatsNew", () => {
  const base = { role: "admin", version: "2.6.10", lastSeen: "2.6.9", playing: false };

  it("shows the popup on a genuine update (admin, not playing)", () => {
    expect(decideWhatsNew(base)).toBe("show");
  });
  it("seeds silently on a fresh install (no last-seen)", () => {
    expect(decideWhatsNew({ ...base, lastSeen: null })).toBe("seed");
  });
  it("seeds (no popup) when the version is unchanged", () => {
    expect(decideWhatsNew({ ...base, lastSeen: "2.6.10" })).toBe("seed");
  });
  it("seeds (no popup) on a downgrade", () => {
    expect(decideWhatsNew({ ...base, version: "2.6.9", lastSeen: "2.6.10" })).toBe("seed");
  });
  it("defers (skip) while a run is actively playing", () => {
    expect(decideWhatsNew({ ...base, playing: true })).toBe("skip");
  });
  it("never shows for a player kiosk", () => {
    expect(decideWhatsNew({ ...base, role: "player" })).toBe("skip");
  });
  it("skips before the version is known", () => {
    expect(decideWhatsNew({ ...base, version: "" })).toBe("skip");
  });
});
