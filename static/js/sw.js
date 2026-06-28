// Themarr Service Worker
// Enables PWA install prompt on Android/Chrome.
// No offline caching — the app requires a live server connection.

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(clients.claim());
});
