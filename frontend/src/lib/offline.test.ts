// The offline preload manager, run against an in-memory CacheStorage: what
// gets cached, what the index reports, what the cap evicts, and what a moving
// look-ahead window aborts.
import { Blob as NodeBlob } from "node:buffer";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cacheStats, cachedPaths, clearOffline, fmtBytes, offlineSupported,
  pinnedRunQueue, pinRunQueue, preloadMany, reconcileIndex, setCacheCapMb,
  setLookahead, touchCached, unpinRunQueue,
} from "./offline";
import { audioUrl } from "./api";

// ── In-memory Cache API ─────────────────────────────────────────────────────
// Keys on the exact string the module passes (it always uses audioUrl(path)
// for put/match/delete, so no URL normalization is needed).

class FakeCache {
  store = new Map<string, Response>();
  async match(url: string) { return this.store.get(url); }
  async put(url: string, resp: Response) { this.store.set(url, resp); }
  async delete(url: string) { return this.store.delete(url); }
}

let fakeCache: FakeCache;

function trackBytes(n: number): Uint8Array {
  return new Uint8Array(n).fill(7);
}

/** fetch stub serving `size` bytes for any /audio GET; HEAD gets the size header. */
function stubFetch(sizes: Record<string, number>, opts?: { failPaths?: Set<string> }) {
  return vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    const u = String(url);
    const path = decodeURIComponent(u.split("path=")[1] ?? "");
    if (opts?.failPaths?.has(path)) return new Response(null, { status: 404 });
    const size = sizes[path] ?? 1000;
    if (init?.method === "HEAD") {
      return new Response(null, { status: 200, headers: { "Content-Length": String(size) } });
    }
    if (init?.signal?.aborted) throw new DOMException("aborted", "AbortError");
    return new Response(trackBytes(size), {
      status: 200,
      headers: { "Content-Type": "audio/mpeg", "Content-Length": String(size) },
    });
  });
}

beforeEach(() => {
  localStorage.clear();
  fakeCache = new FakeCache();
  // Same-realm Blob for the fetch/Response pipeline. In this jsdom + Node
  // combo, `Response` is undici's but the global `Blob` is jsdom's, whose
  // instances carry no stream()/arrayBuffer()/text(). undici's Response.blob()
  // builds its result with the *global* Blob, so offline.ts's re-wrap
  // (`new Response(await resp.blob())`) hands undici a Blob it doesn't
  // recognize as blob-like and the body gets stringified to "[object Blob]"
  // (13 bytes) instead of the track's bytes. Node's buffer.Blob has the full
  // read surface and undici accepts it, on every Node version — real browsers
  // are single-realm and never hit this. Restored by unstubAllGlobals below.
  vi.stubGlobal("Blob", NodeBlob);
  // Recency ordering must be deterministic — real Date.now can hand two
  // writes the same millisecond, making eviction order flip per run.
  let now = 1_000_000_000;
  vi.spyOn(Date, "now").mockImplementation(() => (now += 1000));
  vi.stubGlobal("caches", {
    open: async () => fakeCache,
    delete: async () => { fakeCache.store.clear(); return true; },
  });
  setCacheCapMb(500);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("preloadMany", () => {
  it("downloads every track into the cache and records sizes in the index", async () => {
    vi.stubGlobal("fetch", stubFetch({ a: 100, b: 200 }));
    const done = await preloadMany(["a", "b"]);
    expect(done).toMatchObject({ done: 2, ok: 2, total: 2, bytes: 300 });
    expect(fakeCache.store.has(audioUrl("a"))).toBe(true);
    expect(fakeCache.store.has(audioUrl("b"))).toBe(true);
    expect(cacheStats()).toEqual({ tracks: 2, bytes: 300 });
    // The cached response is a complete 200 the service worker can slice.
    const resp = await fakeCache.match(audioUrl("a"));
    expect(resp!.headers.get("Content-Type")).toBe("audio/mpeg");
    expect((await resp!.blob()).size).toBe(100);
  });

  it("reports failures without caching them, and reports progress as it goes", async () => {
    vi.stubGlobal("fetch", stubFetch({ a: 100, bad: 100 }, { failPaths: new Set(["bad"]) }));
    const seen: number[] = [];
    const done = await preloadMany(["a", "bad"], (p) => seen.push(p.done));
    expect(done.ok).toBe(1);
    expect(done.done).toBe(2);
    expect(fakeCache.store.has(audioUrl("bad"))).toBe(false);
    expect(seen[seen.length - 1]).toBe(2);
  });

  it("skips tracks that are already cached (no second fetch)", async () => {
    const fetchSpy = stubFetch({ a: 100 });
    vi.stubGlobal("fetch", fetchSpy);
    await preloadMany(["a"]);
    await preloadMany(["a"]);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });
});

describe("cap eviction", () => {
  it("evicts the least-recently-touched tracks once past the cap", async () => {
    // Cap of 1 MB; three ~0.5 MB tracks can't all stay.
    setCacheCapMb(1);
    const half = 512 * 1024;
    vi.stubGlobal("fetch", stubFetch({ old: half, mid: half, new: half }));
    await preloadMany(["old"]);
    await preloadMany(["mid"]);
    touchCached("old");                 // "old" is now fresher than "mid"
    await preloadMany(["new"]);         // pushes past the cap → "mid" goes
    expect(fakeCache.store.has(audioUrl("mid"))).toBe(false);
    expect(fakeCache.store.has(audioUrl("old"))).toBe(true);
    expect(fakeCache.store.has(audioUrl("new"))).toBe(true);
    expect(cacheStats().tracks).toBe(2);
  });

  it("never evicts the tracks being preloaded, whatever the order", async () => {
    setCacheCapMb(1);
    const half = 512 * 1024;
    vi.stubGlobal("fetch", stubFetch({ a: half, b: half, c: half }));
    const done = await preloadMany(["a", "b", "c"]);   // one batch: all protected
    expect(done.ok).toBe(3);
    // Over cap while protected — the *next* unprotected write may evict, but
    // the batch itself must finish fully cached.
    expect(await cachedPaths(["a", "b", "c"])).toEqual(new Set(["a", "b", "c"]));
  });
});

describe("setLookahead", () => {
  it("caches the wanted window", async () => {
    vi.stubGlobal("fetch", stubFetch({ n1: 10, n2: 20 }));
    setLookahead(["n1", "n2"]);
    await vi.waitFor(() => expect(cacheStats().tracks).toBe(2));
  });

  it("aborts a look-ahead download that fell out of the window", async () => {
    let sawAbort = false;
    // A fetch that never resolves but honors its abort signal — including one
    // aborted before the fetch even started (the window can move on while the
    // job is still awaiting the cache lookup), which real fetch rejects on.
    vi.stubGlobal("fetch", vi.fn((_url: string, init?: RequestInit) =>
      new Promise((_res, rej) => {
        const onAbort = () => {
          sawAbort = true;
          rej(new DOMException("aborted", "AbortError"));
        };
        if (init?.signal?.aborted) onAbort();
        else init?.signal?.addEventListener("abort", onAbort);
      })));
    setLookahead(["gone"]);
    setLookahead(["kept"]);   // window moved on — "gone" must be aborted
    await vi.waitFor(() => expect(sawAbort).toBe(true));
  });

  it("does nothing when the Cache API is unavailable", () => {
    vi.unstubAllGlobals();
    expect(offlineSupported()).toBe(false);
    expect(() => setLookahead(["x"])).not.toThrow();
  });
});

describe("index self-healing", () => {
  it("reconcileIndex drops entries whose bytes the browser evicted", async () => {
    vi.stubGlobal("fetch", stubFetch({ a: 100, b: 100 }));
    await preloadMany(["a", "b"]);
    fakeCache.store.delete(audioUrl("b"));   // browser storage pressure
    await reconcileIndex();
    expect(cacheStats()).toEqual({ tracks: 1, bytes: 100 });
  });

  it("clearOffline empties the cache, the index and the pinned queues", async () => {
    vi.stubGlobal("fetch", stubFetch({ a: 100 }));
    await preloadMany(["a"]);
    pinRunQueue(165, { tracks: [] });
    await clearOffline();
    expect(cacheStats()).toEqual({ tracks: 0, bytes: 0 });
    expect(fakeCache.store.size).toBe(0);
    expect(pinnedRunQueue(165)).toBeNull();
  });
});

describe("pinned run queues", () => {
  it("stores and retrieves a queue per preset BPM", () => {
    pinRunQueue(155, { tracks: [{ path: "x" }], target: 155 });
    pinRunQueue(175, { tracks: [{ path: "y" }], target: 175 });
    expect(pinnedRunQueue<{ target: number }>(155)?.data.target).toBe(155);
    expect(pinnedRunQueue<{ target: number }>(175)?.data.target).toBe(175);
    expect(pinnedRunQueue(160)).toBeNull();
    unpinRunQueue(155);
    expect(pinnedRunQueue(155)).toBeNull();
    expect(pinnedRunQueue(175)).not.toBeNull();
  });
});

describe("fmtBytes", () => {
  it("formats MB and GB sensibly", () => {
    expect(fmtBytes(0)).toBe("0 MB");
    expect(fmtBytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(fmtBytes(250 * 1024 * 1024)).toBe("250 MB");
    expect(fmtBytes(2 * 1024 * 1024 * 1024)).toBe("2.0 GB");
  });
});
