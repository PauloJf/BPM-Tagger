import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, act, cleanup, waitFor } from "@testing-library/react";
import { PlayerProvider, usePlayer } from "./player";
import { api } from "./api";

// Similar radio on the real PlayerProvider: when the last queued track starts
// and radio is on in "similar" mode, the player asks /api/related/library for
// tracks like it (excluding what was queued recently) and appends them — for
// any queue, with no Listen source needed. Source mode, a tempo lock and a
// repeat mode all leave it alone.

vi.mock("./api", () => ({
  api: {
    post: vi.fn(() => Promise.resolve({ tracks: [] })),
    get: vi.fn(() => Promise.resolve({ tracks: [] })),
  },
  audioUrl: (p: string) => `/audio?path=${encodeURIComponent(p)}`,
  notifyUnauthorized: vi.fn(),
}));

let pc!: ReturnType<typeof usePlayer>;
function Harness() {
  pc = usePlayer();
  return null;
}

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

const A = { path: "/a.mp3", title: "A", artist: "X", bpm: 120 };
const B = { path: "/b.mp3", title: "B", artist: "X", bpm: 122 };
const related = (paths: string[]) => ({
  tracks: paths.map((p) => ({ path: p, title: p, artist: "X", bpm: 121, starred: false,
    loudness_lufs: null, reason: "artist" })),
});
const paths = () => pc.orderedQueue.map((t) => t.path);
const relatedCalls = () => vi.mocked(api.post).mock.calls.filter(([url]) => url === "/api/related/library");

function mount() {
  render(<PlayerProvider><Harness /></PlayerProvider>);
  fakeAudio(document.querySelector("audio")!);
}

beforeEach(() => {
  localStorage.clear();
  vi.mocked(api.post).mockReset();
  vi.mocked(api.post).mockResolvedValue({ tracks: [] });
});
afterEach(() => cleanup());

describe("similar radio", () => {
  it("refills any queue from /api/related/library when the last track starts", async () => {
    vi.mocked(api.post).mockResolvedValue(related(["/c.mp3", "/d.mp3"]));
    mount();
    act(() => { pc.setRadio(true); pc.setRadioMode("similar"); });
    act(() => pc.playQueue([A, B], 1));   // start on the last track: no Listen source
    await waitFor(() => expect(paths()).toEqual(["/a.mp3", "/b.mp3", "/c.mp3", "/d.mp3"]));
    const [, body] = relatedCalls()[0];
    expect(body).toMatchObject({ path: "/b.mp3", count: 20 });
    expect((body as { exclude: string[] }).exclude).toEqual(["/a.mp3", "/b.mp3"]);
  });

  it("drops anything already queued recently", async () => {
    vi.mocked(api.post).mockResolvedValue(related(["/a.mp3", "/c.mp3"]));
    mount();
    act(() => { pc.setRadio(true); pc.setRadioMode("similar"); });
    act(() => pc.playQueue([A, B], 1));
    await waitFor(() => expect(paths()).toEqual(["/a.mp3", "/b.mp3", "/c.mp3"]));
  });

  it("does nothing in source mode without a source", async () => {
    mount();
    act(() => { pc.setRadio(true); pc.setRadioMode("source"); });
    act(() => pc.playQueue([A, B], 1));
    await new Promise((r) => setTimeout(r, 20));
    expect(relatedCalls()).toHaveLength(0);
  });

  it("stays out of a tempo-locked run (the run refill owns it)", async () => {
    mount();
    act(() => { pc.setRadio(true); pc.setRadioMode("similar"); });
    act(() => pc.playQueue([A, B], 1));
    act(() => pc.setTempoLock({ target: 160, octave: true, stretchLimitPct: 15 }));
    vi.mocked(api.post).mockClear();
    act(() => pc.jumpTo(0));
    act(() => pc.jumpTo(1));
    await new Promise((r) => setTimeout(r, 20));
    expect(relatedCalls()).toHaveLength(0);
  });

  it("persists the radio mode", () => {
    mount();
    act(() => { pc.setRadioMode("similar"); pc.playQueue([A]); });
    const saved = JSON.parse(localStorage.getItem("bpm.player") || "{}");
    expect(saved.radioMode).toBe("similar");
  });
});
