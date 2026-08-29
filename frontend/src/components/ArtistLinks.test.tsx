import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { ArtistLinks } from "./ArtistLinks";

vi.mock("react-router-dom", () => ({
  Link: ({ to, children, ...rest }: { to: unknown; children?: unknown }) =>
    <a href={typeof to === "string" ? to : "#"} {...rest}>{children as never}</a>,
}));

afterEach(cleanup);

describe("ArtistLinks", () => {
  it("renders a single artist as one link", () => {
    render(<ArtistLinks artist="Argy" />);
    const link = screen.getByRole("link", { name: "Argy" });
    expect(link.getAttribute("href")).toBe(`/artist?name=${encodeURIComponent("Argy")}`);
  });

  it("splits a multi-artist credit into one link per artist", () => {
    render(<ArtistLinks artist="Argy, SOLANCE" />);
    expect(screen.getByRole("link", { name: "Argy" }).getAttribute("href")).toBe(
      `/artist?name=${encodeURIComponent("Argy")}`);
    expect(screen.getByRole("link", { name: "SOLANCE" }).getAttribute("href")).toBe(
      `/artist?name=${encodeURIComponent("SOLANCE")}`);
  });

  it("does not split a real act name containing '&'", () => {
    render(<ArtistLinks artist="Chase & Status" />);
    expect(screen.getByRole("link", { name: "Chase & Status" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Chase" })).toBeNull();
  });

  it("renders nothing for an empty credit", () => {
    const { container } = render(<ArtistLinks artist="" />);
    expect(container.innerHTML).toBe("");
  });

  it("renders nothing for a null/undefined credit", () => {
    const { container } = render(<ArtistLinks artist={null} />);
    expect(container.innerHTML).toBe("");
  });

  it("applies a prefix before the first link", () => {
    render(<ArtistLinks artist="Argy" prefix="by " />);
    expect(screen.getByText(/by/).textContent).toContain("by");
    expect(screen.getByRole("link", { name: "Argy" })).toBeTruthy();
  });
});
