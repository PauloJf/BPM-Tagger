import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { RatingStars, DislikeButton } from "./RatingStars";

beforeEach(() => cleanup());

describe("RatingStars — full widget", () => {
  it("clicking a star sets that rating", () => {
    const onChange = vi.fn();
    render(<RatingStars value={null} onChange={onChange} />);
    fireEvent.click(screen.getByLabelText("Rate 3 stars"));
    expect(onChange).toHaveBeenCalledWith(3);
  });

  it("clicking the current value clears the rating", () => {
    const onChange = vi.fn();
    render(<RatingStars value={3} onChange={onChange} />);
    fireEvent.click(screen.getByLabelText("Clear rating"));
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it("marks the checked star with the radiogroup's aria-checked, and gives it the roving tabindex", () => {
    render(<RatingStars value={3} onChange={() => {}} />);
    const three = screen.getByLabelText("Clear rating");
    expect(three.getAttribute("aria-checked")).toBe("true");
    expect(three.getAttribute("tabindex")).toBe("0");
    const four = screen.getByLabelText("Rate 4 stars");
    expect(four.getAttribute("aria-checked")).toBe("false");
    expect(four.getAttribute("tabindex")).toBe("-1");
  });

  it("exposes a radiogroup with 5 radio stars", () => {
    render(<RatingStars value={null} onChange={() => {}} />);
    expect(screen.getByRole("radiogroup")).toBeTruthy();
    expect(screen.getAllByRole("radio")).toHaveLength(5);
  });

  it("arrow keys move focus, and Enter/Space select the focused star", () => {
    const onChange = vi.fn();
    render(<RatingStars value={null} onChange={onChange} />);
    const one = screen.getByLabelText("Rate 1 star");
    one.focus();
    fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });
    fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });
    // Focus should have moved to the 3rd star (index 2).
    expect(document.activeElement).toBe(screen.getByLabelText("Rate 3 stars"));
    fireEvent.keyDown(document.activeElement!, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith(3);
  });

  it("Home/End jump to the first/last star", () => {
    render(<RatingStars value={null} onChange={() => {}} />);
    const three = screen.getByLabelText("Rate 3 stars");
    three.focus();
    fireEvent.keyDown(three, { key: "End" });
    expect(document.activeElement).toBe(screen.getByLabelText("Rate 5 stars"));
    fireEvent.keyDown(document.activeElement!, { key: "Home" });
    expect(document.activeElement).toBe(screen.getByLabelText("Rate 1 star"));
  });

  it("readOnly shows the value but responds to no clicks", () => {
    const onChange = vi.fn();
    render(<RatingStars value={4} onChange={onChange} readOnly />);
    fireEvent.click(screen.getByLabelText("Rate 5 stars"));
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("RatingStars — compact variant", () => {
  it("shows a single star + the number, and opens a popover with the full widget", () => {
    render(<RatingStars value={3} onChange={() => {}} compact />);
    expect(screen.getByText("3")).toBeTruthy();
    expect(screen.queryByRole("radiogroup")).toBeNull();
    fireEvent.click(screen.getByLabelText(/Rating: 3 stars/));
    expect(screen.getByRole("radiogroup")).toBeTruthy();
  });

  it("shows a dash for an unrated track", () => {
    render(<RatingStars value={null} onChange={() => {}} compact />);
    expect(screen.getByText("–")).toBeTruthy();
    expect(screen.getByLabelText(/Not rated/)).toBeTruthy();
  });

  it("picking a star in the popover closes it and reports the value", () => {
    const onChange = vi.fn();
    render(<RatingStars value={null} onChange={onChange} compact />);
    fireEvent.click(screen.getByLabelText(/Not rated/));
    fireEvent.click(screen.getByLabelText("Rate 4 stars"));
    expect(onChange).toHaveBeenCalledWith(4);
    expect(screen.queryByRole("radiogroup")).toBeNull();
  });

  it("readOnly compact hides the popover trigger's interactivity", () => {
    const onChange = vi.fn();
    render(<RatingStars value={2} onChange={onChange} compact readOnly />);
    const btn = screen.getByLabelText(/Rating: 2 stars/) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    fireEvent.click(btn);
    expect(screen.queryByRole("radiogroup")).toBeNull();
  });
});

describe("DislikeButton", () => {
  it("toggles and reflects aria-pressed", () => {
    const onToggle = vi.fn();
    const { rerender } = render(<DislikeButton on={false} onToggle={onToggle} />);
    const btn = screen.getByLabelText("Dislike");
    expect(btn.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(btn);
    expect(onToggle).toHaveBeenCalled();
    rerender(<DislikeButton on={true} onToggle={onToggle} />);
    expect(screen.getByLabelText("Remove dislike").getAttribute("aria-pressed")).toBe("true");
  });

  it("readOnly renders nothing (guest view)", () => {
    const { container } = render(<DislikeButton on={false} onToggle={() => {}} readOnly />);
    expect(container.firstChild).toBeNull();
  });
});
