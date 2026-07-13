// Minimal service worker: enables PWA install, does NO caching or fetch
// interception (deliberate — audio streaming and the authenticated API always
// hit the network, and a stale app shell can never be served).
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
