// Browser notifications for pending decisions — both tiers, opt-in (Settings → Notifications).
// Tier 1 (tab open): the Notification API driven by the global SSE bus — any bus event
//   schedules a rate-limited /api/questions diff; unseen open decisions notify once
//   (qid-keyed, remembered in localStorage, OS-deduped via the notification tag).
// Tier 2 (tab closed): Web Push through the service worker at /sw.js — this module only
//   manages the per-browser subscription; the daemon sends the pushes (web/push.py).

import { api } from "/static/api.js";
import { storage } from "/static/util.js";

const ENABLED_KEY = "rsched_notify";        // "on" | anything else = off (opt-in)
const SEEN_KEY = "rsched_notify_seen";      // qids already notified, capped
const CHECK_MIN_MS = 5000;                  // at most one questions fetch per 5s

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

async function check() {
  if (!enabled()) return;
  let qs;
  try { qs = await api("/api/questions"); } catch { return; }
  const seen = seenSet();
  let dirty = false;
  for (const q of qs) {
    if (q.answered || !q.qid || seen.has(q.qid)) continue;
    seen.add(q.qid);
    dirty = true;
    const n = new Notification(`decision needed · ${q.routine}`, {
      body: (q.question || "").replace(/\s+/g, " ").slice(0, 160),
      tag: `rsched-${q.qid}`,               // same decision never stacks up across tabs
    });
    n.onclick = () => { window.focus(); location.hash = "#/questions"; n.close(); };
  }
  if (dirty) storage.set(SEEN_KEY, JSON.stringify([...seen].slice(-200)));
}

let timer = null, lastCheck = 0;

export function initNotifications() {
  if (!supported()) return;
  const schedule = () => {
    if (!enabled()) return;
    clearTimeout(timer);
    const wait = Math.max(0, CHECK_MIN_MS - (Date.now() - lastCheck));
    timer = setTimeout(() => { lastCheck = Date.now(); check(); }, wait);
  };
  window.addEventListener("rsched-bus", schedule);
  schedule();                                // boot: notify whatever is already waiting
}

// ---- tier 2: the per-browser Web Push subscription -------------------------------------------

export const pushSupported = () =>
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
  return sub;
}

export async function pushUnsubscribe() {
  const reg = await navigator.serviceWorker.getRegistration("/");
  const sub = reg && await reg.pushManager.getSubscription();
  if (!sub) return;
  await api("/api/push/unsubscribe", { method: "POST",
    body: { endpoint: sub.endpoint } }).catch(() => {});
  await sub.unsubscribe();
}
