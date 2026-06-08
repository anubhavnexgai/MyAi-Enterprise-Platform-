/* MyAi service worker — network-FIRST so the UI never goes stale.
   The previous version cached the app shell (index.html/app.js/styles.css)
   cache-first, which served an outdated nav after deploys. Now we always hit
   the network and only fall back to cache when offline. */
const CACHE = "myai-shell-v4";
const SHELL = ["/", "/app.js", "/styles.css", "/static/manifest.json", "/static/icon.svg"];

self.addEventListener("install", (e) => {
  // Pre-warm the offline fallback, but don't let it gate activation.
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
      .then(() =>
        // One-time: force any tab still showing the old cached shell to reload
        // onto the fresh, network-first version. Safe (fires once per SW version).
        self.clients.matchAll({ type: "window" }).then((cs) =>
          cs.forEach((c) => {
            try { c.navigate(c.url); } catch (_) {}
          })
        )
      )
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // Never touch API / auth / proxied traffic — always straight to network.
  if (url.pathname.startsWith("/api/") || e.request.method !== "GET") return;

  // Network-first for everything else: fetch fresh, update the offline cache,
  // and only fall back to cache when the network is unavailable. This keeps the
  // app shell (index.html, app.js, styles.css, page fragments) always current.
  e.respondWith(
    fetch(e.request)
      .then((resp) => {
        if (resp && resp.status === 200 && resp.type === "basic") {
          const copy = resp.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
        }
        return resp;
      })
      .catch(() => caches.match(e.request))
  );
});
