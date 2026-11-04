import { describe, it, expect } from "vitest";
import { buildSections, playerItems } from "./Nav";

// The kiosk tabs must mirror the routes PlayerLayout mounts for each listen
// mode (App.tsx) — a drifted pair would offer a dead link or hide a live page.
describe("playerItems — kiosk tabs per listen mode", () => {
  const labels = (mode: string) => playerItems(mode).map((i) => i.label);

  it("off → the original Run-only kiosk", () => {
    expect(labels("off")).toEqual(["Run", "About"]);
  });

  it("on and default → Run + Listen", () => {
    expect(labels("on")).toEqual(["Run", "Listen", "About"]);
    expect(labels("default")).toEqual(["Run", "Listen", "About"]);
  });

  it("only → pure jukebox, Run hidden", () => {
    expect(labels("only")).toEqual(["Listen", "About"]);
  });

  it("an unknown mode degrades to the safe Run-only kiosk", () => {
    expect(labels("sideways")).toEqual(["Run", "About"]);
  });
});

describe("buildSections — sidebar groups per role", () => {
  const groups = (role: string) =>
    buildSections(role).map((s) => `${s.label}: ${s.items.map((i) => i.label).join(", ")}`);

  it("admin gets Play, Library, Tagging and System in pillar order", () => {
    expect(groups("admin")).toEqual([
      "Play: Run, Listen",
      "Library: Browse, Playlists, Cadences",
      "Tagging: BPM Review, Duplicates",
      "System: Stats, Settings, About",
    ]);
  });

  it("member gets no tagging or system pages", () => {
    expect(groups("member")).toEqual([
      "Play: Run, Listen",
      "Library: Browse, Playlists, Cadences",
      "System: About",
    ]);
  });
});
