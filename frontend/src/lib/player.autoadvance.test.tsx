import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, act, cleanup } from "@testing-library/react";
import { PlayerProvider, usePlayer } from "./player";

// Integration test: mount the real PlayerProvider with a faked <audio> element
// and drive the auto-advance path end-to-end through the buffer/preload
// machinery. Verifies the queue advances on `ended` for:
//   1. normal play (stream the next track)
//   2. slow-net buffering (an adaptive rebuffer hold fires, then the track ends)
//   3. slow-net full preload (the next track was downloaded into a blob)
// plus the repeat/end-of-queue semantics that share the same code path.

vi.mock("./api", () => ({
  api: {
    // The mid-run refill hits /api/run/queue; an empty batch just ends the run.
    post: vi.fn(() => Promise.resolve({ tracks: [] })),
    get: vi.fn(() => Promise.resolve({})),
  },
  audioUrl: (p: string) => `/audio?path=${encodeURIComponent(p)}`,
  notifyUnauthorized: vi.fn(),
}));

// The player context, captured fresh on every render of the harness.
let pc!: ReturnType<typeof usePlayer>;
function Harness() {
  pc = usePlayer();
  return null;
}

// Turn jsdom's inert <audio> into a controllable fake: real play/pause/load
// spies, settable currentTime/buffered, a fixed duration. Returns handles so a
// test can move the playhead / buffer and inspect the spies.
function fakeAudio(audio: HTMLAudioElement) {
  let paused = true;
  let currentTime = 0;
  let bufferedEnd = 0;
  const play = vi.fn(() => { paused = false; return Promise.resolve(); });
  const pause = vi.fn(() => { paused = true; });
  const load = vi.fn();
  const def = (name: string, spec: PropertyDescriptor) =>
    Object.defineProperty(audio, name, { configurable: true, ...spec });
  def("play", { value: play });
  def("pause", { value: pause });
  def("load", { value: load });
  def("paused", { get: () => paused });
  def("error", { get: () => null });
  def("duration", { get: () => 180 });
  def("currentTime", { get: () => currentTime, set: (v: number) => { currentTime = v; } });
  def("buffered", {
    get: () => ({
      length: bufferedEnd > 0 ? 1 : 0,
      start: () => 0,
      end: () => bufferedEnd,
    }),
  });
  return {
    play, pause, load,
    setBuffered: (end: number) => { bufferedEnd = end; },
    setTime: (t: number) => { currentTime = t; },
  };
}

const emit = (audio: HTMLAudioElement, type: string) =>
  act(() => { audio.dispatchEvent(new Event(type)); });

const flush = () => new Promise((r) => setTimeout(r, 0));

const A = { path: "/a.mp3", title: "A", bpm: 120 };
const B = { path: "/b.mp3", title: "B", bpm: 120 };
const C = { path: "/c.mp3", title: "C", bpm: 120 };

let audio: HTMLAudioElement;
let fa: ReturnType<typeof fakeAudio>;

function mount() {
  render(<PlayerProvider><Harness /></PlayerProvider>);
  audio = document.querySelector("audio")!;
  fa = fakeAudio(audio);
}

beforeEach(() => {
  localStorage.clear();
  // beginHold and other guards require a visible foreground page.
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
  // Preload blob plumbing.
  let n = 0;
  URL.createObjectURL = vi.fn(() => `blob:mock/${++n}`);
  URL.revokeObjectURL = vi.fn();
  global.fetch = vi.fn(() =>
    Promise.resolve({ ok: true, blob: () => Promise.resolve(new Blob(["x"])) } as unknown as Response));
});

afterEach(() => cleanup());

describe("auto-advance — normal play", () => {
  it("advances to the next track when the current one ends", () => {
    mount();
    act(() => pc.playQueue([A, B]));
    expect(pc.current?.path).toBe("/a.mp3");
    expect(audio.src).toContain("a.mp3");
    const playsBefore = fa.play.mock.calls.length;

    emit(audio, "ended");

    expect(pc.current?.path).toBe("/b.mp3");
    expect(audio.src).toContain("b.mp3");
    expect(fa.play.mock.calls.length).toBeGreaterThan(playsBefore);
  });
});

describe("auto-advance — slow-net buffering", () => {
  it("still advances after an adaptive rebuffer hold engaged and resumed", () => {
    mount();
    act(() => pc.playQueue([A, B]));

    // Underrun with 10s buffered ahead (> the first hold step of 4s), so the
    // hold engages (pauses) then immediately resumes on its first poll.
    fa.setBuffered(10);
    emit(audio, "waiting");
    expect(fa.pause).toHaveBeenCalled();     // the hold paused to accumulate buffer
    expect(pc.buffering).toBe(false);        // ...and resumed once 4s was ahead

    // The track eventually reaches the end and fires `ended`.
    emit(audio, "ended");
    expect(pc.current?.path).toBe("/b.mp3");
  });

  it("advances even if a buffering flag is still set when the track ends", () => {
    // A `waiting` with the whole track buffered short-circuits the hold, leaving
    // `buffering` true with no resume event — the boundary must still advance.
    mount();
    act(() => pc.playQueue([A, B]));
    fa.setBuffered(180);                      // whole track present → hold no-ops
    emit(audio, "waiting");
    expect(pc.buffering).toBe(true);

    emit(audio, "ended");
    expect(pc.current?.path).toBe("/b.mp3");
    expect(pc.buffering).toBe(false);         // reset on the track change
  });
});

describe("auto-advance — slow-net full preload", () => {
  it("plays the next track from its preloaded blob on ended", async () => {
    mount();
    act(() => pc.playQueue([A, B, C]));
    // A tempo lock turns on the run-mode look-ahead that downloads the next
    // tracks into blobs.
    act(() => pc.setTempoLock({ target: 150, octave: true, stretchLimitPct: 15 }));
    await act(async () => { await flush(); await flush(); });

    // B was prefetched, so the boundary plays it from local memory (blob:), not
    // the network.
    expect(URL.createObjectURL).toHaveBeenCalled();
    emit(audio, "ended");
    expect(pc.current?.path).toBe("/b.mp3");
    expect(audio.src.startsWith("blob:")).toBe(true);
  });
});

describe("auto-advance — backgrounded / phone-locked (the run use case)", () => {
  it("advances on ended while the page is hidden", () => {
    // The primary run case: phone locked, PWA backgrounded. The ended->next->
    // goToPos chain must not depend on the page being visible.
    mount();
    act(() => pc.playQueue([A, B]));
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
    Object.defineProperty(document, "hidden", { configurable: true, value: true });

    emit(audio, "ended");
    expect(pc.current?.path).toBe("/b.mp3");
    expect(fa.play).toHaveBeenCalled();
  });

  it("keeps prefetched blobs across a stall pause (look-ahead gated on intent, not `playing`)", async () => {
    // Regression guard for the documented bug: keying the look-ahead on the
    // element's `playing` state (which flips false on every stall pause and at
    // every boundary) revoked the next track's blob right when it was needed, so
    // a backgrounded run streamed at the boundary and could stop advancing.
    mount();
    act(() => pc.playQueue([A, B, C]));
    act(() => pc.setTempoLock({ target: 150, octave: true, stretchLimitPct: 15 }));
    await act(async () => { await flush(); await flush(); });
    const revokedBefore = (URL.revokeObjectURL as ReturnType<typeof vi.fn>).mock.calls.length;

    // A stall pauses the element -> `playing` goes false, but intent holds.
    emit(audio, "pause");
    await act(async () => { await flush(); });

    // The upcoming blob must not have been revoked, so the boundary still plays
    // B from memory.
    expect((URL.revokeObjectURL as ReturnType<typeof vi.fn>).mock.calls.length).toBe(revokedBefore);
    emit(audio, "ended");
    expect(pc.current?.path).toBe("/b.mp3");
    expect(audio.src.startsWith("blob:")).toBe(true);
  });
});

describe("auto-advance — repeat / end-of-queue semantics", () => {
  it("stops advancing at the end of the queue with repeat off", () => {
    mount();
    act(() => pc.playQueue([A, B]));
    emit(audio, "ended");                     // A -> B
    expect(pc.current?.path).toBe("/b.mp3");
    emit(audio, "ended");                     // B -> (end)
    expect(pc.current?.path).toBe("/b.mp3");  // stays on last track
  });

  it("wraps to the front with repeat all", () => {
    mount();
    act(() => pc.playQueue([A, B]));
    act(() => pc.cycleRepeat());              // off -> all
    emit(audio, "ended");                     // A -> B
    emit(audio, "ended");                     // B -> wrap to A
    expect(pc.current?.path).toBe("/a.mp3");
  });

  it("restarts the same track with repeat one", () => {
    mount();
    act(() => pc.playQueue([A, B]));
    act(() => { pc.cycleRepeat(); pc.cycleRepeat(); });   // off -> all -> one
    fa.setTime(120);
    emit(audio, "ended");
    expect(pc.current?.path).toBe("/a.mp3");   // same track
    expect(audio.currentTime).toBe(0);         // rewound
  });
});
