// Browser notifications for pending decisions — both tiers, opt-in (Settings → Notifications).
// Tier 1 (tab open): the Notification API driven by the shared questions store
//   (questions-store.js, which owns the one bus listener and the one fetch for every reader
//   of /api/questions); unseen open decisions notify once (qid-keyed, remembered in
//   localStorage, OS-deduped via the notification tag).
// Tier 2 (tab closed): Web Push through the service worker at /sw.js — this module only
//   manages the per-browser subscription; the daemon sends the pushes (web/push.py).

import { api, syncWorkerToken } from "/static/api.js";
import { loadQuestions, subscribeQuestions } from "/static/questions-store.js";
import { storage } from "/static/util.js";

const ENABLED_KEY = "rsched_notify";        // "on" | anything else = off (opt-in)
const SEEN_KEY = "rsched_notify_seen";      // qids already notified, capped

export const supported = () => "Notification" in window;
export const enabled = () =>
  supported() && storage.get(ENABLED_KEY) === "on" && Notification.permission === "granted";

export async function setEnabled(on) {
  if (!supported()) return false;
  if (on && Notification.permission !== "granted"
      && (await Notification.requestPermission()) !== "granted") return false;
  storage.set(ENABLED_KEY, on ? "on" : "off");
  return on;
}

function seenSet() {
  try { return new Set(JSON.parse(storage.get(SEEN_KEY) || "[]")); }
  catch { return new Set(); }
}

/** Raise ONE tier-1 notification — the only place in the console that constructs one.
 *
 *  Two conventions ride every notification and both are easy to forget at a second call
 *  site: the `tag`, which is how the same event raised from three open tabs collapses into
 *  one OS notification, and the click, which focuses this window and navigates. `href` is a
 *  hash route; omitted, the notification is inert on click. Silently does nothing while
 *  notifications are off — the caller states the event, not the policy. */
export function show(title, body, { tag, href } = {}) {
  if (!enabled()) return null;
  const n = new Notification(title, { body, ...(tag ? { tag } : {}) });
  if (href) n.onclick = () => { window.focus(); location.hash = href; n.close(); };
  return n;
}

function check({ items }) {
  // Checked here, not only in show(): while notifications are off nothing may be marked
  // seen, or switching them on would silently skip the backlog that is already waiting.
  if (!enabled()) return;
  const seen = seenSet();
  let dirty = false;
  for (const q of items) {
    if (q.answered || !q.qid || seen.has(q.qid)) continue;
    seen.add(q.qid);
    dirty = true;
    show(`decision needed · ${q.routine}`,
         (q.question || "").replace(/\s+/g, " ").slice(0, 160),
         { tag: `rsched-${q.qid}`, href: "#/questions" });
  }
  if (dirty) storage.set(SEEN_KEY, JSON.stringify([...seen].slice(-200)));
}

export function initNotifications() {
  if (!supported()) return;
  subscribeQuestions(check);
  loadQuestions().catch(() => { /* offline: the next snapshot notifies */ });
}

// ---- tier 2: the per-browser Web Push subscription -------------------------------------------

const pushSupported = () =>
  "serviceWorker" in navigator && "PushManager" in window && supported();

function b64ToBytes(b64url) {
  const pad = "=".repeat((4 - (b64url.length % 4)) % 4);
  const raw = atob((b64url + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

export async function pushStatus() {
  if (!pushSupported()) return { supported: false, subscribed: false };
  const reg = await navigator.serviceWorker.getRegistration("/");
  const sub = reg ? await reg.pushManager.getSubscription() : null;
  return { supported: true, subscribed: !!sub, endpoint: sub?.endpoint };
}

export async function pushSubscribe() {
  if (!pushSupported()) throw new Error("this browser does not support Web Push");
  if ((await Notification.requestPermission()) !== "granted")
    throw new Error("notification permission was denied");
  const { public_key } = await api("/api/push");
  const reg = await navigator.serviceWorker.register("/sw.js");
  await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: b64ToBytes(public_key),
  });
  await api("/api/push/subscribe", { method: "POST", body: { subscription: sub.toJSON() } });
  await syncWorkerToken();      // sw.js re-registers on rotation, and needs a credential to do it
  return sub;
}

export async function pushReconcile() {
  // The belt to sw.js's braces: Chrome has not always fired pushsubscriptionchange, so a
  // rotated subscription can leave the server holding a dead endpoint while the browser
  // reports itself subscribed. Re-POSTing the live one is an upsert keyed on the endpoint
  // (push.add_subscription), so this is a no-op whenever nothing drifted.
  const reg = await navigator.serviceWorker.getRegistration("/");
  const sub = reg && await reg.pushManager.getSubscription();
  if (!sub) return;
  await api("/api/push/subscribe", { method: "POST", body: { subscription: sub.toJSON() } })
    .catch(() => {});           // a read-only tier or an offline console heals on the next open
  await syncWorkerToken();
}

export async function pushUnsubscribe() {
  const reg = await navigator.serviceWorker.getRegistration("/");
  const sub = reg && await reg.pushManager.getSubscription();
  if (!sub) return;
  await api("/api/push/unsubscribe", { method: "POST",
    body: { endpoint: sub.endpoint } }).catch(() => {});
  await sub.unsubscribe();
}
