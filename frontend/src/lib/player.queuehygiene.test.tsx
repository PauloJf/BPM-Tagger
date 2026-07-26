import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, act, cleanup } from "@testing-library/react";
import { PlayerProvider, usePlayer, type TempoLock } from "./player";

// Queue hygiene on the real PlayerProvider:
//
//  1. `enqueueMany` appends the whole batch. It exists because `enqueue` reads
//     `nav.current`, which an effect only refreshes *after* render — so looping
//     it over N tracks reads one stale snapshot N times and keeps only the last.
//  2. A *new* queue (play / playQueue) exits run mode: the tempo lock and the
//     run source are cleared, so hitting Play on an album or a playlist can't
//     silently hijack a run in progress (stretching it onto your cadence, and
//     letting the auto-refill top it up from the previous run's source).
//  3. Adding to a running queue (enqueue / enqueueMany / playNext) and ducking
//     for a preview do NOT end the run.

vi.mock("./api", () => ({
  api: {
    // The mid-run refill hits /api/run/queue; an empty batch just ends the run.
    post: vi.fn(() => Promise.resolve({ tracks: [] })),
    get: vi.fn(() => Promise.resolve({})),
  },
  audioUrl: (p: string) => `/audio?path=${encodeURIComponent(p)}`,
  notifyUnauthorized: vi.fn(),
}));

let pc!: ReturnType<typeof usePlayer>;
function Harness() {
  pc = usePlayer();
  return null;
}

/** jsdom's <audio> is inert (play() rejects as "not implemented") — give it a
 *  resolving play/pause/load and a duration so the provider's paths run. */
function fakeAudio(audio: HTMLAudioElement) {
  let paused = true;
  const def = (name: string, spec: PropertyDescriptor) =>
    Object.defineProperty(audio, name, { configurable: true, ...spec });
  def("play", { value: vi.fn(() => { paused = false; return Promise.resolve(); }) });
  def("pause", { value: vi.fn(() => { paused = true; }) });
  def("load", { value: vi.fn() });
  def("paused", { get: () => paused });
  def("duration", { get: () => 180 });
  def("readyState", { get: () => 4 });
}

const A = { path: "/a.mp3", title: "A", bpm: 120 };
const B = { path: "/b.mp3", title: "B", bpm: 130 };
const C = { path: "/c.mp3", title: "C", bpm: 140 };
const D = { path: "/d.mp3", title: "D", bpm: 150 };
const LOCK: TempoLock = { target: 155, octave: true, stretchLimitPct: 15 };

function mount() {
  render(<PlayerProvider><Harness /></PlayerProvider>);
  fakeAudio(document.querySelector("audio")!);
}

/** Put a tempo-locked, playlist-scoped run in progress. */
function startRunLike(tracks = [A, B]) {
  act(() => pc.playQueue(tracks));
  act(() => { pc.setRunSource(7); pc.setTempoLock(LOCK); });
  expect(pc.tempoLock).toEqual(LOCK);
  expect(pc.runSource).toBe(7);
}

const paths = () => pc.orderedQueue.map((t) => t.path);

beforeEach(() => {
  localStorage.clear();
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
});

afterEach(() => cleanup());

describe("enqueueMany — appends the whole batch in one write", () => {
  it("appends all N tracks (looping enqueue would drop all but the last)", () => {
    mount();
    act(() => pc.playQueue([A]));
    act(() => pc.enqueueMany([B, C, D]));
    expect(paths()).toEqual(["/a.mp3", "/b.mp3", "/c.mp3", "/d.mp3"]);
  });

  it("is exactly what a loop over enqueue cannot do (the stale-snapshot regression)", () => {
    // Pinned as documentation of *why* enqueueMany exists: enqueue re-reads a
    // `nav.current` that only refreshes after render, so three calls in one tick
    // all append at the same base and only one survives.
    mount();
    act(() => pc.playQueue([A]));
    act(() => { pc.enqueue(B); pc.enqueue(C); pc.enqueue(D); });
    expect(paths().length).toBeLessThan(4);
  });

  it("keeps the current track playing and does not move the playhead", () => {
    mount();
    act(() => pc.playQueue([A, B]));
    act(() => pc.enqueueMany([C, D]));
    expect(pc.current?.path).toBe("/a.mp3");
    expect(pc.orderPos).toBe(0);
  });

  it("starts playback when nothing is queued yet", () => {
    mount();
    act(() => pc.enqueueMany([B, C]));
    expect(pc.current?.path).toBe("/b.mp3");
    expect(paths()).toEqual(["/b.mp3", "/c.mp3"]);
  });

  it("is a no-op for an empty batch", () => {
    mount();
    act(() => pc.playQueue([A]));
    act(() => pc.enqueueMany([]));
    expect(paths()).toEqual(["/a.mp3"]);
    expect(pc.current?.path).toBe("/a.mp3");
  });
});

describe("a new queue exits run mode", () => {
  it("playQueue clears the tempo lock and the run source", () => {
    mount();
    startRunLike();
    act(() => pc.playQueue([C, D]));
    expect(pc.tempoLock).toBeNull();
    expect(pc.runSource).toBeNull();
  });

  it("play (a one-off track) clears the tempo lock and the run source", () => {
    mount();
    startRunLike();
    act(() => pc.play(C));
    expect(pc.tempoLock).toBeNull();
    expect(pc.runSource).toBeNull();
  });

  it("re-setting both after playQueue wins — the Run page's start order", () => {
    // Guards the batching contract Run.tsx's startRun depends on: clear inside
    // playQueue, then re-set, all in one tick → last write per key wins.
    mount();
    act(() => {
      pc.playQueue([C, D]);
      pc.setRunSource("mine");
      pc.setTempoLock(LOCK);
    });
    expect(pc.tempoLock).toEqual(LOCK);
    expect(pc.runSource).toBe("mine");
  });
});

describe("adding to a running queue does NOT end the run", () => {
  it("enqueue keeps the tempo lock and the run source", () => {
    mount();
    startRunLike();
    act(() => pc.enqueue(C));
    expect(pc.tempoLock).toEqual(LOCK);
    expect(pc.runSource).toBe(7);
  });

  it("enqueueMany keeps the tempo lock and the run source", () => {
    mount();
    startRunLike();
    act(() => pc.enqueueMany([C, D]));
    expect(pc.tempoLock).toEqual(LOCK);
    expect(pc.runSource).toBe(7);
    expect(paths()).toEqual(["/a.mp3", "/b.mp3", "/c.mp3", "/d.mp3"]);
  });

  it("playNext keeps the tempo lock and the run source", () => {
    mount();
    startRunLike();
    act(() => pc.playNext(C));
    expect(pc.tempoLock).toEqual(LOCK);
    expect(pc.runSource).toBe(7);
  });

  it("preview keeps the tempo lock and the run source (it ducks and returns)", () => {
    mount();
    startRunLike();
    act(() => pc.preview({ path: "preview:dz:1", title: "Clip", ephemeral: true }));
    expect(pc.tempoLock).toEqual(LOCK);
    expect(pc.runSource).toBe(7);
  });
});
