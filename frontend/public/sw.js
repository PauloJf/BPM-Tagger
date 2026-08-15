// Service worker: PWA install + offline audio.
//
// The app shell and the authenticated API are deliberately NEVER cached — a
// stale shell or stale JSON must never be served. The one thing this worker
// does beyond enabling install is serve /audio streams cache-first from the
// "bpm-audio-v1" cache, which the app fills explicitly (the player's
// look-ahead and the Run page's "Prepare offline" — see
// frontend/src/lib/offline.ts). The <audio> element always plays the same
// /audio URL; whether the bytes come from this cache or the network is
// invisible to it — that indirection is the whole design, replacing the old
// blob-URL preload whose blob-vs-stream source switching broke track advance
// on real devices (see the PRELOAD_BLOBS post-mortem in git history).
//
// Uncached /audio requests fall straight through to the network, so a fetch
// handler failure can never make streaming worse than before.

const AUDIO_CACHE = "bpm-audio-v1";
const AUDIO_PATH = "/audio";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

/** Parse an HTTP Range header against a resource of `size` bytes.
 *  Returns {start, end} (inclusive), null for "no/unsupported range" (serve
 *  the full 200), or "invalid" for an unsatisfiable range (serve 416).
 *  Handles the three single-range forms: bytes=A-B, bytes=A-, bytes=-N.
 *  Multi-range requests are not used by media elements — served as full 200.
 *  KEEP IN SYNC with the vitest suite in src/lib/offline.range.test.ts. */
function parseRange(header, size) {
  if (!header || size <= 0) return null;
  const m = /^bytes=(\d*)-(\d*)$/.exec(header.trim());
  if (!m) return null;                      // multi-range or malformed → full response
  const [, a, b] = m;
  if (a === "" && b === "") return null;
  if (a === "") {
    // Suffix form bytes=-N: the last N bytes.
    const n = Number(b);
    if (n === 0) return "invalid";
    return { start: Math.max(0, size - n), end: size - 1 };
  }
  const start = Number(a);
  if (start >= size) return "invalid";
  const end = b === "" ? size - 1 : Math.min(Number(b), size - 1);
  if (end < start) return "invalid";
  return { start, end };
}

/** Serve an /audio request from the preload cache, honoring Range (Safari
 *  refuses media that answers a Range request with a plain 200). Falls back
 *  to the network when the track isn't cached. */
async function serveAudio(request) {
  let cached;
  try {
    const cache = await caches.open(AUDIO_CACHE);
    // The app caches full 200 responses keyed by URL; ignore Vary so the
    // element's Range/Accept headers can't cause a spurious miss.
    cached = await cache.match(request.url, { ignoreVary: true });
  } catch {
    cached = undefined;
  }
  if (!cached) return fetch(request);
  const blob = await cached.blob();
  const type = cached.headers.get("Content-Type") || "application/octet-stream";
  const range = parseRange(request.headers.get("Range"), blob.size);
  if (range === "invalid") {
    return new Response(null, {
      status: 416,
      headers: { "Content-Range": `bytes */${blob.size}` },
    });
  }
  if (!range) {
    return new Response(blob, {
      status: 200,
      headers: {
        "Content-Type": type,
        "Content-Length": String(blob.size),
        "Accept-Ranges": "bytes",
      },
    });
  }
  const part = blob.slice(range.start, range.end + 1);
  return new Response(part, {
    status: 206,
    headers: {
      "Content-Type": type,
      "Content-Length": String(part.size),
      "Content-Range": `bytes ${range.start}-${range.end}/${blob.size}`,
      "Accept-Ranges": "bytes",
    },
  });
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || url.pathname !== AUDIO_PATH) return;
  event.respondWith(serveAudio(request));
});

// Test hook: lets the vitest suite import this file and exercise parseRange
// without a real ServiceWorkerGlobalScope. Harmless in production.
self.__bpmSw = { parseRange };
