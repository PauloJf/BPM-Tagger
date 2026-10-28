import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

const h = vi.hoisted(() => ({ pathname: "/tracks", search: "" }));

vi.mock("react-router-dom", () => ({
  Link: ({ to, children, ...rest }: { to: unknown; children?: unknown }) =>
    <a href={typeof to === "string" ? to : "#"} {...rest}>{children as never}</a>,
  useLocation: () => ({ pathname: h.pathname, search: h.search }),
}));

import LibraryTabs from "./LibraryTabs";

const href = (name: string) => screen.getByRole("link", { name }).getAttribute("href");

beforeEach(() => {
  cleanup();
  h.pathname = "/tracks";
  h.search = "";
});

describe("LibraryTabs — the tempo filter follows you", () => {
  it("links the plain views when no tempo is set", () => {
    render(<LibraryTabs />);
    expect([href("Tracks"), href("Artists"), href("Albums")]).toEqual(["/tracks", "/artists", "/albums"]);
  });

  it("carries the tempo filter between Tracks and Artists", () => {
    h.search = "?bpm=155&bpm_tol=5&bpm_cadence=1&filter=starred&page=3";
    render(<LibraryTabs />);
    expect(href("Artists")).toBe("/artists?bpm=155&bpm_tol=5&bpm_cadence=1");
    expect(href("Tracks")).toBe("/tracks?bpm=155&bpm_tol=5&bpm_cadence=1");
  });

  it("leaves Albums alone, since it has no tempo filter", () => {
    h.search = "?bpm=155&bpm_tol=5";
    render(<LibraryTabs />);
    expect(href("Albums")).toBe("/albums");
  });

  it("carries nothing out of Albums", () => {
    h.pathname = "/albums";
    h.search = "?bpm=155";
    render(<LibraryTabs />);
    expect(href("Artists")).toBe("/artists");
  });
});
