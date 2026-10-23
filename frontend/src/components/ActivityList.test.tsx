import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import ActivityList from "./ActivityList";

afterEach(cleanup);

describe("ActivityList", () => {
  it("renders nothing when no background work is running", () => {
    const { container } = render(<ActivityList items={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("shows a count and a bar for a job that knows its total", () => {
    render(<ActivityList items={[{ id: "loudness", label: "Measuring loudness", done: 120, total: 900 }]} />);
    expect(screen.getByText("Measuring loudness")).toBeTruthy();
    expect(screen.getByText("120 / 900")).toBeTruthy();
    const fill = document.querySelector(".sidebar-scan-fill") as HTMLElement;
    expect(fill.style.width).toBe("13%");
  });

  it("shows only the label (no count, no bar) while a job is still counting or untracked", () => {
    render(<ActivityList items={[
      { id: "isrc", label: "Looking up ISRCs", done: 0, total: null },
      { id: "sync", label: "Syncing with Navidrome", done: null, total: null },
    ]} />);
    expect(screen.getByText("Looking up ISRCs")).toBeTruthy();
    expect(screen.getByText("Syncing with Navidrome")).toBeTruthy();
    expect(document.querySelector(".sidebar-scan-fill")).toBeNull();
    expect(document.querySelector(".sidebar-scan-count")).toBeNull();
  });

  it("never overflows the bar", () => {
    render(<ActivityList items={[{ id: "waveform", label: "Building waveforms", done: 12, total: 10 }]} />);
    expect((document.querySelector(".sidebar-scan-fill") as HTMLElement).style.width).toBe("100%");
  });
});
