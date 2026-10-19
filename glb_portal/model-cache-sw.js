// Keep at most two large models on this browser. Every reuse first checks the
// authenticated HEAD response, so a stale or signed-out page cannot use a hit.
const CACHE_NAME = 'glb-portal-models-v1';
const MODEL_URL = /^\/api\/models\/[0-9a-f]{32}\/file$/;

self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));

async function tellPage(event, status) {
  try {
    const client = event.clientId && await self.clients.get(event.clientId);
    client?.postMessage({ type: 'model-cache', modelId: new URL(event.request.url).pathname.split('/')[3], status });
  } catch { /* Status messages must never interrupt loading. */ }
}

async function getModel(event) {
  const request = event.request;
  let check;
  try {
    check = await fetch(new Request(request, { method: 'HEAD', cache: 'no-store' }));
  } catch {
    // A network check is required even for a cached private model.
    return fetch(request);
  }
  let cache;
  try { cache = await caches.open(CACHE_NAME); }
  catch { return fetch(request); }
  if (!check.ok) {
    try { await cache.delete(request); } catch { /* Let the server decide access. */ }
    return fetch(request);
  }
  const version = check.headers.get('ETag');
  if (!version) return fetch(request);
  let hit;
  try { hit = await cache.match(request); }
  catch { return fetch(request); }
  if (hit?.headers.get('ETag') === version) {
    await tellPage(event, 'hit');
    return hit;
  }
  if (hit) {
    try { await cache.delete(request); } catch { /* A fresh download still works. */ }
  }
  await tellPage(event, 'downloading');
  const response = await fetch(request);
  if (response.ok && response.headers.get('ETag') === version) {
    event.waitUntil((async () => {
      try {
        await cache.put(request, response.clone());
        const entries = await cache.keys();
        while (entries.length > 2) await cache.delete(entries.shift());
        await tellPage(event, 'saved');
      } catch {
        await tellPage(event, 'unavailable');
      }
    })());
  }
  return response;
}

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET' || new URL(event.request.url).origin !== self.location.origin || !MODEL_URL.test(new URL(event.request.url).pathname)) return;
  event.respondWith(getModel(event));
});
