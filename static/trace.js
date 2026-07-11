// UI interaction traces — friction evidence for the self-audit's improve-ui lens.
// Batched fire-and-forget: navigations, control clicks (by visible label), error toasts,
// stream reconnects, and uncaught JS errors. No free-text input values are ever recorded.
// Flushes every 5s / 20 events via fetch; on pagehide via fetch keepalive (sendBeacon
// cannot carry the Authorization header).

import { getToken } from "/static/api.js";

const FLUSH_MS = 5000;
const FLUSH_AT = 20;
const queue = [];
let timer = null;

function currentView() {
  return (location.hash || "#/").slice(2).split(/[/?]/)[0] || "dashboard";
}

export function trace(kind, target = "", detail = "") {
  queue.push({ kind, view: currentView(), target: String(target).slice(0, 200),
               detail: String(detail).slice(0, 200) });
  if (queue.length >= FLUSH_AT) flush();
  else if (!timer) timer = setTimeout(flush, FLUSH_MS);
}

function flush(keepalive = false) {
  if (timer) { clearTimeout(timer); timer = null; }
  if (!queue.length) return;
  const token = getToken();
  if (!token) { queue.length = 0; return; }   // pre-auth events are gate noise, drop them
  const events = queue.splice(0, FLUSH_AT * 2);
  fetch("/api/ui-trace", {
    method: "POST",
    keepalive,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ events }),
  }).catch(() => {});   // tracing must never surface as UI noise itself
}

export function installTracing() {
  window.addEventListener("hashchange", () => trace("nav", currentView()));
  document.addEventListener("click", (e) => {
    const el = e.target.closest("button, a[href^='#/']");
    if (!el) return;
    const label = (el.textContent || "").trim().slice(0, 40);
    if (label && el.type !== "password") trace("click", label);
  }, { capture: true, passive: true });
  window.addEventListener("error", (e) => trace("error", "js", String(e.message).slice(0, 200)));
  window.addEventListener("unhandledrejection",
    (e) => trace("error", "promise", String(e.reason).slice(0, 200)));
  window.addEventListener("pagehide", () => flush(true));
}
