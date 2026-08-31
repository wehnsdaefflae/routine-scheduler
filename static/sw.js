// Web Push service worker (tier 2 notifications) — served at /sw.js so its scope is the
// whole console. Deliberately minimal: no caching, no offline — the daemon self-updates
// and the fresh_ui middleware handles staleness. This worker turns pushes into
// notifications, clicks into a focused Decisions page, and a browser-side subscription
// rotation back into a subscription the server knows about.

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; }
  catch { data = { body: event.data && event.data.text() }; }
  const tag = data.tag || "rsched-decision";   // one notification per decision, however many pushes
  if (data.close) {
    // The decision was answered (or withdrawn): the server sends a same-tag "close" push so we
    // clear the tray notification instead of showing another one — no stale alert for a settled
    // decision.
    event.waitUntil(self.registration.getNotifications({ tag })
      .then((ns) => ns.forEach((n) => n.close())));
    return;
  }
  event.waitUntil(self.registration.showNotification(data.title || "rsched", {
    body: data.body || "a routine needs a decision",
    tag,
    data: { url: data.url || "/#/questions" },
  }));
});

// A browser may retire a push subscription on its own (key rotation, a storage sweep, a long
// silence). The server only learns of the dead endpoint when it next pushes and the service
// answers 404/410 — by which time that notification is already lost, and every later one is too:
// nothing re-registers, and Settings still reports "subscribed" because the BROWSER has a
// subscription, just not the one the server stores. So re-register here, on the spot.
// AUTH_CACHE/AUTH_ENTRY are PAIRED WITH static/api.js (a worker cannot read localStorage) —
// change them in one file only and this goes silently dead.
const AUTH_CACHE = "rsched-auth";
const AUTH_ENTRY = "/__auth_token";

async function authHeaders() {
  const cache = await caches.open(AUTH_CACHE);
  const hit = await cache.match(AUTH_ENTRY);
  if (!hit) return null;                       // no credential cached — heal on the next visit
  const token = (await hit.text()).trim();
  return token ? { Authorization: `Bearer ${token}`, "Content-Type": "application/json" } : null;
}

self.addEventListener("pushsubscriptionchange", (event) => {
  event.waitUntil((async () => {
    const headers = await authHeaders();
    if (!headers) return;
    const old = event.oldSubscription && event.oldSubscription.endpoint;
    if (old) {
      await fetch("/api/push/unsubscribe", { method: "POST", headers,
        body: JSON.stringify({ endpoint: old }) }).catch(() => {});
    }
    // Chrome fires this event with neither subscription populated — re-subscribe ourselves
    // then, reusing the server's VAPID key (applicationServerKey takes the base64url string).
    let sub = event.newSubscription;
    if (!sub) {
      const info = await (await fetch("/api/push", { headers })).json();
      sub = await self.registration.pushManager.subscribe({
        userVisibleOnly: true, applicationServerKey: info.public_key });
    }
    await fetch("/api/push/subscribe", { method: "POST", headers,
      body: JSON.stringify({ subscription: sub.toJSON() }) });
  })());
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
