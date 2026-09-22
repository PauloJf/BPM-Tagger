import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, act, cleanup } from "@testing-library/react";
import { PlayerProvider, usePlayer } from "./player";

// playQueue() used to take `startIndex = 0` and, when shuffling, pin that track
// to the head and shuffle only the rest. Every shuffle-all caller passed a
// literal 0, so the first track was never in the draw: the whole-library
// shuffle opened with the alphabetically-first artist's first track every
// single time. Anchoring is still supported, but only when asked for.

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

const TRACKS = ["a", "b", "c", "d"].map((n) => ({ path: `/${n}.mp3`, title: n.toUpperCase(), bpm: 120 }));

beforeEach(() => {
  localStorage.clear();
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
});
afterEach(() => { vi.restoreAllMocks(); cleanup(); });

function mount() {
  render(<PlayerProvider><Harness /></PlayerProvider>);
  const audio = document.querySelector("audio")!;
  Object.defineProperty(audio, "play", { configurable: true, value: vi.fn(() => Promise.resolve()) });
  Object.defineProperty(audio, "pause", { configurable: true, value: vi.fn() });
  Object.defineProperty(audio, "load", { configurable: true, value: vi.fn() });
}

describe("playQueue shuffle", () => {
  it("does not pin the first track when no anchor is given", () => {
    // Math.random()=0 makes Fisher-Yates fully deterministic: for [0,1,2,3] it
    // swaps each i with index 0 in turn, giving [1,2,3,0]. The point is simply
    // that index 0 is no longer guaranteed to lead.
    vi.spyOn(Math, "random").mockReturnValue(0);
    mount();
    act(() => pc.playQueue(TRACKS, undefined, { shuffle: true }));
    expect(pc.current?.path).toBe("/b.mp3");
  });

  it("puts every track in the draw across many shuffles", () => {
    mount();
    const firsts = new Set<string>();
    for (let i = 0; i < 40; i++) {
      act(() => pc.playQueue(TRACKS, undefined, { shuffle: true }));
      firsts.add(pc.current!.path);
    }
    // Real randomness, but 40 draws over 4 tracks: seeing fewer than two
    // distinct leaders is a ~1-in-10^23 fluke, so this is effectively a
    // guarantee that the head is not pinned.
    expect(firsts.size).toBeGreaterThan(1);
  });

  it("still anchors the given track when an index is passed", () => {
    mount();
    for (let i = 0; i < 10; i++) {
      act(() => pc.playQueue(TRACKS, 2, { shuffle: true }));
      expect(pc.current?.path).toBe("/c.mp3");
    }
  });

  it("keeps listed order and the given start when shuffle is off", () => {
    mount();
    act(() => pc.playQueue(TRACKS, 2, { shuffle: false }));
    expect(pc.current?.path).toBe("/c.mp3");
    act(() => pc.next());
    expect(pc.current?.path).toBe("/d.mp3");
  });

  it("starts at the top when shuffle is off and no index is given", () => {
    mount();
    act(() => pc.playQueue(TRACKS, undefined, { shuffle: false }));
    expect(pc.current?.path).toBe("/a.mp3");
  });
});
