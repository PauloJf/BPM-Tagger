import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, act, cleanup } from "@testing-library/react";
import { PlayerProvider, usePlayer } from "./player";

// reorderTo() backs the queue drawer's drag-and-drop. It must move a row to an
// arbitrary index and keep `orderPos` pointing at whatever is actually playing,
// regardless of which rows moved around it.

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
const C = { path: "/c.mp3", title: "C", bpm: 120 };
const D = { path: "/d.mp3", title: "D", bpm: 120 };

beforeEach(() => {
  localStorage.clear();
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
});
afterEach(() => cleanup());

function mount() {
  render(<PlayerProvider><Harness /></PlayerProvider>);
  // Make jsdom's inert <audio> harmless for these state-only tests.
  const audio = document.querySelector("audio")!;
  Object.defineProperty(audio, "play", { configurable: true, value: vi.fn(() => Promise.resolve()) });
  Object.defineProperty(audio, "pause", { configurable: true, value: vi.fn() });
  Object.defineProperty(audio, "load", { configurable: true, value: vi.fn() });
}

const paths = () => pc.orderedQueue.map((t) => t.path);

describe("reorderTo", () => {
  it("moves a row down to an arbitrary position", () => {
    mount();
    act(() => pc.playQueue([A, B, C, D]));   // playing A at pos 0
    act(() => pc.reorderTo(0, 2));           // move A into slot 2
    expect(paths()).toEqual(["/b.mp3", "/c.mp3", "/a.mp3", "/d.mp3"]);
    // A is still playing, now at index 2.
    expect(pc.orderPos).toBe(2);
    expect(pc.current?.path).toBe("/a.mp3");
  });

  it("moves a row up and keeps orderPos on the playing track", () => {
    mount();
    act(() => pc.playQueue([A, B, C, D]));
    act(() => pc.jumpTo(1));                  // now playing B at pos 1
    act(() => pc.reorderTo(3, 0));            // drag D to the top
    expect(paths()).toEqual(["/d.mp3", "/a.mp3", "/b.mp3", "/c.mp3"]);
    // B moved from index 1 to index 2; orderPos follows it.
    expect(pc.orderPos).toBe(2);
    expect(pc.current?.path).toBe("/b.mp3");
  });

  it("ignores out-of-range and no-op moves", () => {
    mount();
    act(() => pc.playQueue([A, B, C]));
    act(() => pc.reorderTo(0, 0));
    act(() => pc.reorderTo(5, 1));
    act(() => pc.reorderTo(1, -1));
    expect(paths()).toEqual(["/a.mp3", "/b.mp3", "/c.mp3"]);
    expect(pc.orderPos).toBe(0);
  });
});
