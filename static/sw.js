// Web Push service worker (tier 2 notifications) — served at /sw.js so its scope is the
// whole console. Deliberately minimal: no caching, no offline — the daemon self-updates
// and the fresh_ui middleware handles staleness; this worker only turns pushes into
// notifications and clicks into a focused Decisions page.

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; }
  catch { data = { body: event.data && event.data.text() }; }
  event.waitUntil(self.registration.showNotification(data.title || "rsched", {
    body: data.body || "a routine needs a decision",
    tag: data.tag || "rsched-decision",     // one notification per decision, however many pushes
    data: { url: data.url || "/#/questions" },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/#/questions";
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true })
    .then((wins) => {
      for (const w of wins) {
        if ("focus" in w) { w.navigate(url); return w.focus(); }
      }
      return self.clients.openWindow(url);
    }));
});
