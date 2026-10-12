const CACHE_NAME = 'qrpipe-receiver-static-v1';
const STATIC_FILES = [
  './', './index.html', './app.js', './styles.css', './manifest.webmanifest',
  './icon.svg', './sw.js', './THIRD_PARTY_NOTICES.md',
];

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);
    await cache.addAll(STATIC_FILES.map((path) => new URL(path, self.registration.scope)));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((key) => key.startsWith('qrpipe-receiver-static-') && key !== CACHE_NAME)
      .map((key) => caches.delete(key)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET' || new URL(request.url).origin !== self.location.origin) return;
  event.respondWith((async () => {
    const requestUrl = new URL(request.url);
    const staticPaths = STATIC_FILES.map((path) => new URL(path, self.registration.scope).pathname);
    if (!staticPaths.includes(requestUrl.pathname)) return new Response('Not found', { status: 404 });
    const cached = await caches.match(request);
    if (cached) return cached;
    return fetch(request);
  })());
});
