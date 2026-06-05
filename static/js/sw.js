// Bump this version whenever the caching logic changes — `activate` purges every
// older cache, which also flushes any bad responses cached by a previous version.
const CACHE_NAME = 'ki-crm-shell-v3';
const ASSETS = [
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      // Cache shell assets individually so one missing file can't abort install.
      .then(cache => Promise.allSettled(ASSETS.map(a => cache.add(a))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.map(key => key !== CACHE_NAME && caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

// Only cache genuinely good, same-origin responses — never a 404/500/opaque body.
function isCacheable(response) {
  return response && response.ok && response.status === 200 && response.type === 'basic';
}

self.addEventListener('fetch', event => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const isStatic = request.url.includes('/static/') ||
    ['style', 'script', 'image', 'font'].includes(request.destination);

  if (!isStatic) {
    // Navigations/API: network-first, fall back to cache only when offline.
    event.respondWith(fetch(request).catch(() => caches.match(request)));
    return;
  }

  // Static assets: stale-while-revalidate. Serve cache instantly when present,
  // but always refresh it in the background so updates (and any previously
  // cached bad response) are replaced on the next load.
  event.respondWith(
    caches.open(CACHE_NAME).then(cache =>
      cache.match(request).then(cached => {
        const network = fetch(request).then(response => {
          if (isCacheable(response)) cache.put(request, response.clone());
          return response;
        }).catch(() => cached);
        return cached || network;
      })
    )
  );
});
