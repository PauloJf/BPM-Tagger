// Last-page memory: what gets remembered, what gets restored, and — above all
// — what must NOT be redirected (deep links, browser tabs, bogus storage).
import { beforeEach, describe, expect, it } from "vitest";
import { lastPage, rememberPage, restoreTarget } from "./lastPage";

beforeEach(() => localStorage.clear());

describe("lastPage validation", () => {
  it("round-trips a normal in-app page with its query", () => {
    rememberPage("/playlist?id=3");
    expect(lastPage()).toBe("/playlist?id=3");
  });

  it("refuses anything that isn't a sane in-app path", () => {
    for (const bad of ["", "/", "/login", "/login?x=1", "//evil.example", "https://evil.example", "tracks"]) {
      localStorage.setItem("bpm.lastPage", bad);
      expect(lastPage(), bad).toBeNull();
    }
  });
});

describe("restoreTarget (PWA start_url launch)", () => {
  it("restores the saved page when the installed app boots at bare /run", () => {
    expect(restoreTarget("/run", true, "/listen")).toBe("/listen");
    expect(restoreTarget("/run", true, "/playlist?id=3")).toBe("/playlist?id=3");
  });

  it("stays put when the saved page IS /run", () => {
    expect(restoreTarget("/run", true, "/run")).toBeNull();
  });

  it("never redirects a browser tab (not standalone)", () => {
    expect(restoreTarget("/run", false, "/listen")).toBeNull();
  });

  it("never redirects a deep link (?bpm= keeps its query) or another page", () => {
    expect(restoreTarget("/run?bpm=165", true, "/listen")).toBeNull();
    expect(restoreTarget("/settings", true, "/listen")).toBeNull();
  });

  it("does nothing with nothing saved", () => {
    expect(restoreTarget("/run", true, null)).toBeNull();
  });
});
