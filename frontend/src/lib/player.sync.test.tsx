import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, act, cleanup } from "@testing-library/react";
import { PlayerProvider, usePlayer } from "./player";

// Cross-device sync: the PlayerProvider mirrors its queue snapshot to
// /api/player/state (per account) and adopts the server copy on boot when
// another device wrote since this browser last looked. These tests drive the
// real provider with a mocked auth context and a fetch fake for the sync
// endpoint (playerSync deliberately uses raw fetch, not the api wrapper).

vi.mock("./api", () => ({
  api: {
    post: vi.fn(() => Promise.resolve({ tracks: [] })),
    get: vi.fn(() => Promise.resolve({})),
  },
  audioUrl: (p: string) => `/audio?path=${encodeURIComponent(p)}`,
  notifyUnauthorized: vi.fn(),
  getCsrfToken: () => "test-csrf",
}));

// The provider only syncs when an AuthProvider reports an authenticated
// session; fake one rather than mounting the real auth stack.
vi.mock("./auth", () => ({
  useAuthOptional: () => ({ authenticated: true, normalizePlayback: false, loudnessTargetLufs: -14 }),
}));

let pc!: ReturnType<typeof usePlayer>;
function Harness() {
  pc = usePlayer();
  return null;
}

const A = { path: "/a.mp3", title: "A", bpm: 120 };
const B = { path: "/b.mp3", title: "B", bpm: 130 };

const SERVER_SNAPSHOT = {
  queue: [A, B], order: [0, 1], pos: 1, shuffle: false, repeat: "off" as const,
  volume: 0.5, time: 42, playing: true, tempoLock: null,
  runSource: null, listenSource: null, radio: false,
};

// fetch fake: GET returns the configured server state; PUT records the pushed
// snapshots and stamps them.
let serverGet: { sync: boolean; state: unknown; updated_at: string | null };
let puts: { state: unknown }[];

function fakeFetch(url: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  if (String(url).includes("/api/player/state")) {
    if (!init || !init.method || init.method === "GET") {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(serverGet) } as unknown as Response);
    }
    if (init.method === "PUT") {
      puts.push(JSON.parse(String(init.body)));
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ ok: true, sync: true, updated_at: `stamp-${puts.length}` }),
      } as unknown as Response);
    }
  }
  return Promise.resolve({ ok: true, json: () => Promise.resolve({}), blob: () => Promise.resolve(new Blob()) } as unknown as Response);
}

const flush = () => act(() => new Promise<void>((r) => setTimeout(r, 0)));

// jsdom's <audio> is inert (play() returns undefined) — give it just enough of
// a media surface for playQueue's load-and-play path to run.
function stubAudio() {
  const audio = document.querySelector("audio")!;
  let paused = true;
  const def = (name: string, spec: PropertyDescriptor) =>
    Object.defineProperty(audio, name, { configurable: true, ...spec });
  def("play", { value: vi.fn(() => { paused = false; return Promise.resolve(); }) });
  def("pause", { value: vi.fn(() => { paused = true; }) });
  def("load", { value: vi.fn() });
  def("paused", { get: () => paused });
}

function mount() {
  render(<PlayerProvider><Harness /></PlayerProvider>);
  stubAudio();
}

beforeEach(() => {
  localStorage.clear();
  puts = [];
  serverGet = { sync: true, state: null, updated_at: null };
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  global.fetch = vi.fn(fakeFetch) as unknown as typeof fetch;
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

describe("cross-device sync — adoption on boot", () => {
  it("adopts an unseen server snapshot, paused, at the saved position", async () => {
    serverGet = { sync: true, state: SERVER_SNAPSHOT, updated_at: "2026-07-31T10:00:00" };
    mount();
    await flush();

    expect(pc.current?.path).toBe("/b.mp3");       // queue[order[1]]
    expect(pc.queue.map((t) => t.path)).toEqual(["/a.mp3", "/b.mp3"]);
    expect(pc.playing).toBe(false);                 // never autoplays on adoption
    // The stamp is now "seen": a re-mount with the same stamp keeps local state.
    expect(localStorage.getItem("bpm.player.serverStamp")).toBe("2026-07-31T10:00:00");
  });

  it("keeps local state when the server stamp was already seen", async () => {
    localStorage.setItem("bpm.player.serverStamp", "2026-07-31T10:00:00");
    serverGet = { sync: true, state: SERVER_SNAPSHOT, updated_at: "2026-07-31T10:00:00" };
    mount();
    await flush();
    expect(pc.current).toBe(null);
    expect(pc.queue).toEqual([]);
  });

  it("never adopts for a non-syncing (Guest) account", async () => {
    serverGet = { sync: false, state: null, updated_at: null };
    mount();
    await flush();
    expect(pc.current).toBe(null);
  });
});

describe("cross-device sync — pushing local changes", () => {
  it("pushes the queue to the server after a local change (debounced)", async () => {
    vi.useFakeTimers();
    mount();
    await act(async () => { await vi.runOnlyPendingTimersAsync(); });   // boot GET arms sync

    act(() => pc.playQueue([A, B]));
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); }); // > PUSH_DEBOUNCE_MS

    expect(puts.length).toBeGreaterThan(0);
    const pushed = puts[puts.length - 1].state as typeof SERVER_SNAPSHOT;
    expect(pushed.queue.map((t) => t.path)).toEqual(["/a.mp3", "/b.mp3"]);
  });

  it("does not push for a non-syncing (Guest) account", async () => {
    vi.useFakeTimers();
    serverGet = { sync: false, state: null, updated_at: null };
    mount();
    await act(async () => { await vi.runOnlyPendingTimersAsync(); });

    act(() => pc.playQueue([A, B]));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(puts).toEqual([]);
  });

  it("sign-out drops the local queue without wiping the server copy", async () => {
    vi.useFakeTimers();
    mount();
    await act(async () => { await vi.runOnlyPendingTimersAsync(); });

    act(() => pc.playQueue([A, B]));
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    const pushesBefore = puts.length;
    expect(pushesBefore).toBeGreaterThan(0);

    act(() => { window.dispatchEvent(new Event("bpm:sign-out")); });
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });

    expect(pc.queue).toEqual([]);                   // this device's queue is gone
    expect(puts.length).toBe(pushesBefore);         // …but nothing was pushed after sign-out
    expect(puts.every((p) => p.state !== null)).toBe(true);
  });
});
