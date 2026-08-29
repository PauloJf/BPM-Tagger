import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, act, cleanup } from "@testing-library/react";
import { PlayerProvider, usePlayer } from "./player";

// isQueued() backs the library row's add-to-queue button state. Regression:
// it used to read the raw `queue` array, which is an append-only backing
// store — removeAt() only trims `order`, so a removed track's entry lingers
// in `queue` and would show as queued forever. Must track `order` instead.

vi.mock("./api", () => ({
  api: { post: vi.fn(() => Promise.resolve({ tracks: [] })), get: vi.fn(() => Promise.resolve({})) },
  audioUrl: (p: string) => `/audio?path=${encodeURIComponent(p)}`,
  notifyUnauthorized: vi.fn(),
}));

let pc!: ReturnType<typeof usePlayer>;
function Harness() {
  pc = usePlayer();
  return null;
}

const A = { path: "/a.mp3", title: "A", bpm: 120 };
const B = { path: "/b.mp3", title: "B", bpm: 120 };

beforeEach(() => {
  localStorage.clear();
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
});
afterEach(() => cleanup());

function mount() {
  render(<PlayerProvider><Harness /></PlayerProvider>);
  const audio = document.querySelector("audio")!;
  Object.defineProperty(audio, "play", { configurable: true, value: vi.fn(() => Promise.resolve()) });
  Object.defineProperty(audio, "pause", { configurable: true, value: vi.fn() });
  Object.defineProperty(audio, "load", { configurable: true, value: vi.fn() });
}

describe("isQueued", () => {
  it("is false before anything plays", () => {
    mount();
    expect(pc.isQueued("/a.mp3")).toBe(false);
  });

  it("is true once a track is playing", () => {
    mount();
    act(() => pc.play(A));
    expect(pc.isQueued("/a.mp3")).toBe(true);
  });

  it("is true for a track appended via enqueue", () => {
    mount();
    act(() => pc.play(A));
    act(() => pc.enqueue(B));
    expect(pc.isQueued("/b.mp3")).toBe(true);
  });

  it("goes false again after the track is removed from the queue", () => {
    mount();
    act(() => pc.playQueue([A, B]));
    expect(pc.isQueued("/b.mp3")).toBe(true);
    const pos = pc.orderedQueue.findIndex((t) => t.path === "/b.mp3");
    act(() => pc.removeAt(pos));
    expect(pc.isQueued("/b.mp3")).toBe(false);
  });

  it("goes false for every track once the queue is stopped", () => {
    mount();
    act(() => pc.playQueue([A, B]));
    act(() => pc.stop());
    expect(pc.isQueued("/a.mp3")).toBe(false);
    expect(pc.isQueued("/b.mp3")).toBe(false);
  });
});
