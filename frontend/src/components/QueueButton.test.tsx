import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { QueueButton } from "./trackBits";

const h = vi.hoisted(() => ({
  queuedPaths: new Set<string>(),
  orderedQueue: [] as { path: string }[],
  enqueued: [] as { path: string }[],
  removedAt: [] as number[],
}));

vi.mock("../lib/player", () => ({
  usePlayer: () => ({
    isQueued: (p: string) => h.queuedPaths.has(p),
    orderedQueue: h.orderedQueue,
    enqueue: (t: { path: string }) => h.enqueued.push(t),
    removeAt: (pos: number) => h.removedAt.push(pos),
  }),
}));

beforeEach(() => {
  cleanup();
  h.queuedPaths = new Set();
  h.orderedQueue = [];
  h.enqueued = [];
  h.removedAt = [];
});

describe("QueueButton", () => {
  const track = { path: "/m/a.mp3", title: "A" };

  it("shows an 'Add to queue' state and enqueues the track when not queued", () => {
    render(<QueueButton track={track} />);
    const b = screen.getByRole("button", { name: "Add to queue" });
    expect(b.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(b);
    expect(h.enqueued).toEqual([track]);
  });

  it("reflects a queued track with a different state — the bug being fixed", () => {
    h.queuedPaths = new Set(["/m/a.mp3"]);
    render(<QueueButton track={track} />);
    const b = screen.getByRole("button", { name: "Remove from queue" });
    expect(b.getAttribute("aria-pressed")).toBe("true");
  });

  it("clicking a queued track removes it from the queue by position", () => {
    h.queuedPaths = new Set(["/m/a.mp3"]);
    h.orderedQueue = [{ path: "/m/other.mp3" }, { path: "/m/a.mp3" }];
    render(<QueueButton track={track} />);
    fireEvent.click(screen.getByRole("button", { name: "Remove from queue" }));
    expect(h.removedAt).toEqual([1]);
    expect(h.enqueued).toEqual([]);
  });
});
