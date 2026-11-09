// UI interaction traces — friction evidence for the self-audit's improve-ui lens.
// Batched fire-and-forget: navigations, control clicks (by visible label), error toasts,
// stream reconnects, and uncaught JS errors. No free-text input values are ever recorded.
// Each event carries the time it HAPPENED (`ts`, stamped in trace() below) — batching decides
// when a record is SENT, never when it occurred.
// Flushes every 5s / 20 events via fetch; on pagehide via fetch keepalive (sendBeacon
// cannot carry the Authorization header).

import { getToken, onRequestTiming, openStreamCount } from "/static/api.js";

const FLUSH_MS = 5000;
const FLUSH_AT = 20;
const queue = [];
let timer = null;

function currentView() {
  return (location.hash || "#/").slice(2).split(/[/?]/)[0] || "conversations";
}

// F577: the time of the EVENT, taken here — not at flush. A batch is flushed every 5s/20
// events (and a `pagehide` flush can carry a whole session's tail), so the server's receive
// stamp quantises every interval inside a flush window to zero — and interval is the one
// quantity the friction readers (improve-ui, the self-audit's evidence pass) measure. The
// server keeps its own `received` stamp beside this one and only trusts this value inside a
// sane skew window, so a wrong client clock is visible rather than silently believed.
export function trace(kind, target = "", detail = "") {
  queue.push({ kind, view: currentView(), ts: new Date().toISOString(),
               target: String(target).slice(0, 200),
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
    if (label) trace("click", label);   // a button's or a link's own text — never a field's
  }, { capture: true, passive: true });
  window.addEventListener("error", (e) => trace("error", "js", String(e.message).slice(0, 200)));
  window.addEventListener("unhandledrejection",
    (e) => trace("error", "promise", String(e.reason).slice(0, 200)));
  window.addEventListener("pagehide", () => flush(true));
  installFreezeObserver();
  installRequestTiming();
  installLoadTiming();
  installRenderTiming();
}

// F861: how long a VIEW takes to settle after a navigation — the cost this console mostly is.
// app.js is a hash router: only the FIRST page load of a session fires `load`, and every
// navigation after it swaps #view's children in place. The store's own numbers say how lopsided
// that is (752 `nav` rows against roughly one page load per session over 14 days), so `load` and
// `slow` between them miss what the operator meets most: clicking through to a page and waiting
// for it. A view can feel slow while every request it makes is fast — three sequential 400 ms
// fetches, a large render, a skeleton that lingers.
//
// It lives HERE rather than in app.js for two reasons: that file is at its size-ratchet ceiling
// and may not grow, and a tracer that watches the DOM needs nothing from the router — the app
// does not know it is measured, which is the better seam.
//
// "Settled" is a QUIET PERIOD, not a paint: the row is filed once no mutation has arrived for
// SETTLE_MS. The run view streams, so its mutations never truly stop — the observer disarms
// after reporting, so one navigation yields at most one row and a live view cannot inflate it.
const SETTLE_MS = 150;
const RENDER_MAX_MS = 30000;   // a backgrounded tab resuming must not book its absence as render

function installRenderTiming() {
  const view = document.getElementById("view");
  if (!view || typeof MutationObserver === "undefined") return;
  let armed = false, startedAt = 0, quiet = null;
  const obs = new MutationObserver(() => {
    if (!armed) return;
    clearTimeout(quiet);
    quiet = setTimeout(() => {
      if (!armed) return;
      armed = false;                       // one row per navigation, whatever streams after
      const ms = Math.round(performance.now() - startedAt) - SETTLE_MS;
      if (ms < 0 || ms > RENDER_MAX_MS) return;   // implausible: dropped, never recorded
      trace("render", currentView(), `${ms}ms to settle`);
    }, SETTLE_MS);
  });
  obs.observe(view, { childList: true, subtree: true });
  window.addEventListener("hashchange", () => {
    clearTimeout(quiet);
    armed = true;
    startedAt = performance.now();
  });
}

// F858: how long the console's own requests take, and how long a page takes to become usable.
// The store held 2,016 clicks and 819 navigations over its whole 14-day window and not one
// duration — so "the console is slow" could be reported but never measured. The freeze observer
// below does not cover it: Long Tasks fire for work that BLOCKS the main thread, and waiting on
// a request blocks nothing (0 `freeze` rows in 14 days).
//
// Only requests OVER the floor become rows. Fifteen `setInterval` pollers run in this console
// (app.js 30 s, run.js 3 s, conversations.js 20 s, ribbon.js 120 s …), so recording every
// request would bury the signal under its own volume; a request the user waits on is the
// subject. api.js measures and reports — the floor, the store and the wording live here,
// because policy belongs to the tracer and not to the transport every view imports.
const SLOW_REQUEST_MS = 1000;

// The PATH only. A query string can carry what the operator typed (a search term, a filter) and
// this module records no free-text input — while which endpoint is slow is the whole question.
function endpointOf(path) { return String(path).split("?")[0]; }

function installRequestTiming() {
  onRequestTiming((path, ms, status) => {
    if (ms < SLOW_REQUEST_MS) return;
    trace("slow", endpointOf(path),
          `${ms}ms, status ${status}, ${openStreamCount()} streams open`);
  });
}

// One row per page load, from the browser's own Navigation Timing: `response` is when the
// server had answered the document, `dom` when the console's modules had parsed and run, and
// `load` when everything the page asked for was in. Which of the three is large says where the
// cost is — the server, the JS, or the assets — and no guess can substitute for that split.
function installLoadTiming() {
  window.addEventListener("load", () => {
    // After `load` the entry is final; a timeout lets the last milestone be written first.
    setTimeout(() => {
      const nav = performance.getEntriesByType?.("navigation")?.[0];
      if (!nav) return;                       // unsupported browser → nothing to report
      const ms = (v) => Math.round(v || 0);
      trace("load", location.pathname,
            `response ${ms(nav.responseEnd)}ms, dom ${ms(nav.domContentLoadedEventEnd)}ms, `
            + `load ${ms(nav.loadEventEnd)}ms`);
      flush();                                // a load row is worth its own POST
    }, 0);
  });
}

// F218: main-thread FREEZE telemetry. A UI freeze IS a long task — the browser's Long Tasks
// API fires for any task that blocks the main thread ≥50ms (a jank the user feels as a stall).
// We record the WORST block per throttle window as a `freeze` trace (view + blocked-ms), so the
// next audit can measure freezes and correlate them with reconnect bursts instead of guessing
// whether they happen. Throttled to one report per FREEZE_WINDOW_MS and floored at a higher
// threshold so routine sub-50ms jank is not logged — only real stalls are evidence.
const FREEZE_MIN_MS = 200;
const FREEZE_WINDOW_MS = 10000;
function installFreezeObserver() {
  if (typeof PerformanceObserver === "undefined") return;   // unsupported browser → skip
  let worst = 0, lastReport = 0, pending = null;
  try {
    const obs = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        if (entry.duration < FREEZE_MIN_MS) continue;
        worst = Math.max(worst, Math.round(entry.duration));
        if (pending) continue;
        const wait = Math.max(0, FREEZE_WINDOW_MS - (Date.now() - lastReport));
        pending = setTimeout(() => {
          pending = null; lastReport = Date.now();
          const ms = worst; worst = 0;
          // F263: stamp the concurrent open-stream count so a freeze can be correlated
          // with stream fan-out (the connection-exhaustion / network-stall hypothesis).
          trace("freeze", "main-thread", `blocked ${ms}ms, ${openStreamCount()} streams open`);
        }, wait);
      }
    });
    obs.observe({ entryTypes: ["longtask"] });
  } catch { /* longtask entry type unsupported — nothing to observe */ }
}
