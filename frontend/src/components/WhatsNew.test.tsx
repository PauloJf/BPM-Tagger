import { describe, it, expect } from "vitest";
import { parseChangelog, cmpVersion, sectionsSince, parseBody } from "./Changelog";
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

describe("changelog body blocks", () => {
  it("turns `###` lines into headings, not literal paragraphs", () => {
    const b = parseBody("### Cadence-ready views\n\n- A thing\n");
    expect(b[0]).toEqual({ kind: "heading", text: "Cadence-ready views" });
    expect(JSON.stringify(b)).not.toContain("#");
  });

  it("keeps a lead-in line as a paragraph", () => {
    expect(parseBody("**Playlists grow up**\n")[0]).toEqual({
      kind: "para", text: "**Playlists grow up**",
    });
  });

  it("groups consecutive bullets into one list and records indent depth", () => {
    const b = parseBody("- top\n  - nested\n- top again\n");
    expect(b.length).toBe(1);
    expect(b[0]).toEqual({
      kind: "list",
      items: [
        { text: "top", depth: 0 },
        { text: "nested", depth: 1 },
        { text: "top again", depth: 0 },
      ],
    });
  });

  it("starts a new list after a heading", () => {
    const b = parseBody("- a\n\n### Next\n\n- b\n");
    expect(b.map((x) => x.kind)).toEqual(["list", "heading", "list"]);
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
