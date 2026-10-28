import { describe, it, expect } from "vitest";
import { buildSections } from "./Nav";

// The admin nav groups pages by intent. The sidebar and the mobile menu both read
// buildSections, so pinning it here covers both.
describe("buildSections — admin navigation groups", () => {
  const sections = buildSections();

  it("orders the sections Play, Library, Maintain, System", () => {
    expect(sections.map((s) => s.label)).toEqual(["Play", "Library", "Maintain", "System"]);
  });

  it("puts the runner's pages first and the tagger's pages under Maintain", () => {
    const labels = (name: string) => sections.find((s) => s.label === name)!.items.map((i) => i.label);
    expect(labels("Play")).toEqual(["Home", "Listen"]);
    expect(labels("Library")).toEqual(["Library", "Playlists"]);
    expect(labels("Maintain")).toEqual(["BPM Review", "Duplicates", "Stats"]);
    expect(labels("System")).toEqual(["Settings", "About"]);
  });

  it("keeps every destination reachable exactly once", () => {
    const routes = sections.flatMap((s) => s.items.map((i) => i.to));
    expect(routes.sort()).toEqual(
      ["/about", "/duplicates", "/home", "/listen", "/playlists", "/review", "/settings", "/stats", "/tracks"],
    );
  });

  it("has no separate Run entry: the Run | Listen switch on the page covers it, and the entry stays lit on /run", () => {
    const listen = sections.flatMap((s) => s.items).find((i) => i.label === "Listen")!;
    expect(sections.flatMap((s) => s.items).some((i) => i.label === "Run")).toBe(false);
    expect(listen.match).toEqual(["/listen", "/run"]);
  });

  it("keeps the review badge on BPM Review only", () => {
    const badged = sections.flatMap((s) => s.items).filter((i) => i.badge).map((i) => i.label);
    expect(badged).toEqual(["BPM Review"]);
  });
});
