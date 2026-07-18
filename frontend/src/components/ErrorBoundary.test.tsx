import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import ErrorBoundary from "./ErrorBoundary";

function Boom(): JSX.Element {
  throw new Error("boom");
}

afterEach(cleanup);

describe("ErrorBoundary", () => {
  it("renders its children when nothing throws", () => {
    render(<ErrorBoundary><div>healthy content</div></ErrorBoundary>);
    expect(screen.getByText("healthy content")).toBeTruthy();
  });

  it("shows a reload fallback when a child throws (no white screen)", () => {
    // React logs the caught error to console.error — silence it for clean output.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(<ErrorBoundary><Boom /></ErrorBoundary>);
    expect(screen.getByText("Something went wrong")).toBeTruthy();
    expect(screen.getByRole("button", { name: /reload/i })).toBeTruthy();
    spy.mockRestore();
  });
});
