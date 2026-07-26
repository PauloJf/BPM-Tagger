import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { FontStepper, useDrawerFont, type DrawerFont } from "./DrawerControls";

beforeEach(() => {
  cleanup();
  localStorage.clear();
});

describe("FontStepper", () => {
  it("renders the four S/M/L/XL steps and marks the active one", () => {
    render(<FontStepper font="l" onChange={() => {}} />);
    expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["S", "M", "L", "XL"]);
    expect(screen.getByRole("button", { name: "L" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "XL" }).getAttribute("aria-pressed")).toBe("false");
  });

  it("reports the picked size via onChange", () => {
    const onChange = vi.fn();
    render(<FontStepper font="m" onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "XL" }));
    expect(onChange).toHaveBeenCalledWith("xl");
  });
});

describe("useDrawerFont", () => {
  // A tiny harness that surfaces the hook's value and lets a click change it.
  function Harness({ storageKey }: { storageKey: string }) {
    const [font, setFont] = useDrawerFont(storageKey);
    return (
      <div>
        <span data-testid="font">{font}</span>
        <button onClick={() => setFont("xl")}>set-xl</button>
      </div>
    );
  }

  it("defaults to medium when nothing is stored", () => {
    render(<Harness storageKey="bpm-test-font" />);
    expect(screen.getByTestId("font").textContent).toBe("m");
  });

  it("reads a previously stored size", () => {
    localStorage.setItem("bpm-test-font", "l");
    render(<Harness storageKey="bpm-test-font" />);
    expect(screen.getByTestId("font").textContent).toBe("l");
  });

  it("ignores a garbage stored value", () => {
    localStorage.setItem("bpm-test-font", "huge");
    render(<Harness storageKey="bpm-test-font" />);
    expect(screen.getByTestId("font").textContent).toBe("m");
  });

  it("persists a change to localStorage", () => {
    render(<Harness storageKey="bpm-test-font" />);
    fireEvent.click(screen.getByText("set-xl"));
    expect(screen.getByTestId("font").textContent).toBe("xl");
    expect(localStorage.getItem("bpm-test-font")).toBe("xl");
  });

  it("keeps separate keys independent (queue vs lyrics)", () => {
    localStorage.setItem("bpm-queue-font", "s");
    localStorage.setItem("bpm-lyrics-font", "xl");
    render(<Harness storageKey="bpm-queue-font" />);
    expect(screen.getByTestId("font").textContent).toBe("s");
    cleanup();
    render(<Harness storageKey="bpm-lyrics-font" />);
    expect(screen.getByTestId("font").textContent).toBe("xl");
  });
});

// Type-level guard: the DrawerFont union is what the queue/lyrics classes rely on.
const _sizes: DrawerFont[] = ["s", "m", "l", "xl"];
void _sizes;
