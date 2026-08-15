// Offline preloading: download whole tracks into the Cache API so the service
// worker (frontend/public/sw.js) can serve them when the network is gone.
//
// The design constraint, learned the hard way (see the PRELOAD_BLOBS
// post-mortem in git history): the <audio> element must never switch source
// types. It always plays the same /audio?path=… URL; this module only decides
// which of those URLs have their bytes stored locally. Two producers fill the
// cache:
//   • the player's look-ahead (setLookahead) — the next few tracks of the
//     active queue, whatever page built it;
//   • the Run page's "Prepare offline" (preloadMany) — the pinned per-preset
//     queues, downloaded explicitly by the user.
// A localStorage index tracks sizes and recency so the cache can be capped
// (oldest-first eviction) and reported in the UI. The Cache API only exists
// in secure contexts (https or localhost) — everything here degrades to a
// no-op over plain http, and offlineSupported() lets the UI say so.

import { audioUrl } from "./api";

// Must match AUDIO_CACHE in frontend/public/sw.js.
const CACHE_NAME = "bpm-audio-v1";
const INDEX_KEY = "bpm.offline.index";
const RUNQ_KEY = "bpm.offline.runQueues";

// Enforced cache cap; the authenticated boot (auth.tsx) overrides it from the
// server's preload_cache_mb setting.
let capMb = 500;
export function setCacheCapMb(mb: number) {
  if (isFinite(mb) && mb > 0) capMb = mb;
}

export function offlineSupported(): boolean {
  return typeof caches !== "undefined";
}

// ── Size/recency index ──────────────────────────────────────────────────────
// The Cache API stores bytes but no metadata, so sizes and last-use times live
// in localStorage. The cache is the source of truth for *presence* (the index
// self-heals against it); the index is the source of truth for size and age.

interface IndexEntry { size: number; at: number }
type OfflineIndex = Record<string, IndexEntry>;

function readIndex(): OfflineIndex {
  try {
    const v = JSON.parse(localStorage.getItem(INDEX_KEY) || "{}");
    return v && typeof v === "object" ? (v as OfflineIndex) : {};
  } catch {
    return {};
  }
}

function writeIndex(idx: OfflineIndex) {
  try {
    localStorage.setItem(INDEX_KEY, JSON.stringify(idx));
  } catch { /* ignore — quota; the cache still works, only stats degrade */ }
}

/** Synchronous cache stats from the index — for settings/status UI. */
export function cacheStats(): { tracks: number; bytes: number } {
  const idx = readIndex();
  let bytes = 0, tracks = 0;
  for (const k in idx) { tracks += 1; bytes += idx[k].size || 0; }
  return { tracks, bytes };
}

/** Bump a cached track's recency (called for the currently playing track) so
 *  cap eviction drops what hasn't been touched longest, not what's playing. */
export function touchCached(path: string) {
  const idx = readIndex();
  if (idx[path]) { idx[path].at = Date.now(); writeIndex(idx); }
}

// ── Cache operations ────────────────────────────────────────────────────────

async function openCache(): Promise<Cache | null> {
  if (!offlineSupported()) return null;
  try { return await caches.open(CACHE_NAME); } catch { return null; }
}

/** Which of `paths` are actually in the cache (not just in the index). */
export async function cachedPaths(paths: string[]): Promise<Set<string>> {
  const out = new Set<string>();
  const cache = await openCache();
  if (!cache) return out;
  await Promise.all(paths.map(async (p) => {
    try {
      if (await cache.match(audioUrl(p), { ignoreVary: true })) out.add(p);
    } catch { /* treat as uncached */ }
  }));
  return out;
}

async function removeFromCache(cache: Cache, path: string, idx: OfflineIndex) {
  try { await cache.delete(audioUrl(path), { ignoreVary: true }); } catch { /* ignore */ }
  delete idx[path];
}

/** Evict oldest-first until the cache fits the cap. `protect` (the current
 *  look-ahead window + whatever is playing) is never evicted. */
async function evictToCap(cache: Cache, protect: Set<string>) {
  const idx = readIndex();
  const capBytes = capMb * 1024 * 1024;
  let total = 0;
  for (const k in idx) total += idx[k].size || 0;
  if (total <= capBytes) return;
  const evictable = Object.keys(idx)
    .filter((p) => !protect.has(p))
    .sort((a, b) => idx[a].at - idx[b].at);
  for (const path of evictable) {
    if (total <= capBytes) break;
    total -= idx[path].size || 0;
    await removeFromCache(cache, path, idx);
  }
  writeIndex(idx);
}

/** Drop the whole offline cache (and the pinned run queues). */
export async function clearOffline(): Promise<void> {
  for (const [, job] of inflight) job.ctrl.abort();
  inflight.clear();
  try { localStorage.removeItem(INDEX_KEY); } catch { /* ignore */ }
  try { localStorage.removeItem(RUNQ_KEY); } catch { /* ignore */ }
  if (offlineSupported()) {
    try { await caches.delete(CACHE_NAME); } catch { /* ignore */ }
  }
}

/** Prune index entries whose bytes are gone (the browser can evict Cache
 *  Storage under pressure) so stats and "ready" badges never overreport. */
export async function reconcileIndex(): Promise<void> {
  const cache = await openCache();
  if (!cache) return;
  const idx = readIndex();
  const paths = Object.keys(idx);
  if (!paths.length) return;
  const present = await cachedPaths(paths);
  let changed = false;
  for (const p of paths) {
    if (!present.has(p)) { delete idx[p]; changed = true; }
  }
  if (changed) writeIndex(idx);
}

// ── Downloads ───────────────────────────────────────────────────────────────
// One in-flight table for both producers, so the look-ahead and a manual
// "Prepare offline" never download the same track twice. Look-ahead jobs
// (pinned=false) are aborted when the window moves past them; manual jobs
// survive window changes.

interface Job { ctrl: AbortController; pinned: boolean; promise: Promise<boolean> }
const inflight = new Map<string, Job>();

async function isCached(cache: Cache, path: string): Promise<boolean> {
  try { return !!(await cache.match(audioUrl(path), { ignoreVary: true })); } catch { return false; }
}

/** Download one track into the cache. Resolves true when the track is cached
 *  (already or newly), false on failure/abort. Never throws. */
function preloadOne(path: string, pinned: boolean, protect: Set<string>): Promise<boolean> {
  const existing = inflight.get(path);
  if (existing) {
    // A manual download must not be left abortable by a moving look-ahead.
    if (pinned) existing.pinned = true;
    return existing.promise;
  }
  const ctrl = new AbortController();
  const promise = (async () => {
    const cache = await openCache();
    if (!cache) return false;
    if (await isCached(cache, path)) {
      touchCached(path);
      return true;
    }
    const init: RequestInit & { priority?: string } = {
      credentials: "same-origin",
      signal: ctrl.signal,
    };
    // Look-ahead yields bandwidth to the playing stream (ignored where unsupported).
    if (!pinned) init.priority = "low";
    const resp = await fetch(audioUrl(path), init);
    if (!resp.ok || resp.status !== 200) return false;
    // Read the full body first: a Response built from the blob is guaranteed
    // complete (a network drop mid-body rejects here instead of caching a
    // truncated track), and gives the exact size for the index.
    const blob = await resp.blob();
    await cache.put(audioUrl(path), new Response(blob, {
      headers: {
        "Content-Type": resp.headers.get("Content-Type") || "application/octet-stream",
        "Content-Length": String(blob.size),
      },
    }));
    const idx = readIndex();
    idx[path] = { size: blob.size, at: Date.now() };
    writeIndex(idx);
    await evictToCap(cache, protect);
    return true;
  })().catch(() => false).finally(() => { inflight.delete(path); });
  inflight.set(path, { ctrl, pinned, promise });
  return promise;
}

/** The player's look-ahead: make sure `paths` (the next few tracks of the
 *  active queue, in play order) are cached. Aborts look-ahead downloads that
 *  fell out of the window; leaves manual downloads alone. `playing` is the
 *  current track — protected from eviction alongside the window. */
export function setLookahead(paths: string[], playing?: string | null) {
  if (!offlineSupported()) return;
  const want = new Set(paths);
  for (const [path, job] of inflight) {
    if (!job.pinned && !want.has(path)) { job.ctrl.abort(); inflight.delete(path); }
  }
  const protect = new Set(paths);
  if (playing) protect.add(playing);
  for (const path of paths) void preloadOne(path, false, protect);
}

export interface PreloadProgress {
  done: number;    // tracks finished (cached or failed)
  total: number;
  ok: number;      // tracks confirmed cached
  bytes: number;   // bytes now cached across the requested tracks
}

/** Manual bulk download ("Prepare offline"): fetch every path, two at a time,
 *  reporting progress. Resolves with the final tally. */
export async function preloadMany(
  paths: string[],
  onProgress?: (p: PreloadProgress) => void,
): Promise<PreloadProgress> {
  const state: PreloadProgress = { done: 0, total: paths.length, ok: 0, bytes: 0 };
  if (!offlineSupported() || !paths.length) return state;
  const protect = new Set(paths);
  const queue = paths.slice();
  const worker = async () => {
    for (;;) {
      const path = queue.shift();
      if (path == null) return;
      const ok = await preloadOne(path, true, protect);
      state.done += 1;
      if (ok) {
        state.ok += 1;
        state.bytes += readIndex()[path]?.size || 0;
      }
      onProgress?.({ ...state });
    }
  };
  await Promise.all([worker(), worker()]);
  return state;
}

/** Sum of Content-Length over HEAD requests — the size estimate shown before
 *  a "Prepare offline" download. `known` counts tracks that reported a size. */
export async function estimateSize(paths: string[]): Promise<{ bytes: number; known: number }> {
  let bytes = 0, known = 0;
  const queue = paths.slice();
  const worker = async () => {
    for (;;) {
      const path = queue.shift();
      if (path == null) return;
      try {
        const r = await fetch(audioUrl(path), { method: "HEAD", credentials: "same-origin" });
        const len = Number(r.headers.get("Content-Length"));
        if (r.ok && isFinite(len) && len > 0) { bytes += len; known += 1; }
      } catch { /* unknown size — skip */ }
    }
  };
  await Promise.all([worker(), worker(), worker(), worker()]);
  return { bytes, known };
}

// ── Pinned run queues ───────────────────────────────────────────────────────
// /api/run/queue randomizes every build, so "the preset's queue" only exists
// once it's pinned: Prepare offline stores the exact queue it downloaded, and
// startRun falls back to it when the server is unreachable. Keyed by preset
// BPM; a re-prepare overwrites.

export interface PinnedRunQueue<T = unknown> { bpm: number; at: number; data: T }

function readRunQueues(): Record<string, PinnedRunQueue> {
  try {
    const v = JSON.parse(localStorage.getItem(RUNQ_KEY) || "{}");
    return v && typeof v === "object" ? (v as Record<string, PinnedRunQueue>) : {};
  } catch {
    return {};
  }
}

export function pinRunQueue<T>(bpm: number, data: T) {
  const all = readRunQueues();
  all[String(bpm)] = { bpm, at: Date.now(), data };
  try { localStorage.setItem(RUNQ_KEY, JSON.stringify(all)); } catch { /* ignore */ }
}

export function pinnedRunQueue<T>(bpm: number): PinnedRunQueue<T> | null {
  return (readRunQueues()[String(bpm)] as PinnedRunQueue<T> | undefined) ?? null;
}

export function unpinRunQueue(bpm: number) {
  const all = readRunQueues();
  delete all[String(bpm)];
  try { localStorage.setItem(RUNQ_KEY, JSON.stringify(all)); } catch { /* ignore */ }
}

export function fmtBytes(n: number): string {
  if (!isFinite(n) || n <= 0) return "0 MB";
  const mb = n / (1024 * 1024);
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`;
  return `${mb >= 100 ? Math.round(mb) : mb.toFixed(1)} MB`;
}
