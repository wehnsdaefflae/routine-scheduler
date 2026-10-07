// ONE reader of /api/desktops for the whole console — the desktop dock and the Desktops page
// both show the fleet, and two pollers of one endpoint would make the daemon's load depend on
// which page happens to be open (CLAUDE.md: one reader per endpoint, a second consumer
// SUBSCRIBES).
//
// The broker publishes nothing onto the bus, so this POLLS — and only while somebody can see
// the result: a hidden tab skips the round, coming back reads at once, and with no subscriber
// left (the dock never mounted, the page torn down) the timer stops altogether. Every read is
// the broker's in-memory list behind one async proxy hop, which is what makes a few seconds an
// honest cadence for "up 3 min · idle 20 s".

import { api } from "/static/api.js";

const EVERY_MS = 5000;

let snapshot = null;          // { slots, idle_limit_s, desktops: [...] } | { error }
let inflight = null;
let timer = null;
const subscribers = new Set();

function fetchNow() {
  if (inflight) return inflight;
  inflight = api("/api/desktops")
    .then((fleet) => { snapshot = fleet; })
    // A failed read is a STATE to show (the broker is down, a secret is missing), never an
    // empty fleet: "nothing is running" and "cannot tell" must not look alike.
    .catch((err) => { snapshot = { error: err.message || String(err), desktops: [] }; })
    .then(() => {
      for (const fn of [...subscribers]) {
        try { fn(snapshot); } catch { /* one bad reader must not starve the others */ }
      }
      return snapshot;
    })
    .finally(() => { inflight = null; });
  return inflight;
}

function tick() {
  if (!document.hidden) fetchNow();
}

function arm() {
  if (timer || !subscribers.size) return;
  timer = setInterval(tick, EVERY_MS);
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden && subscribers.size) fetchNow();
});

/** Read now (joining a read already in flight); resolves to the snapshot, never rejects. */
export function loadDesktops() { return fetchNow(); }

/** Register a reader of every read from now on; returns its unsubscribe, which stops the poll
 *  once nobody is left. */
export function subscribeDesktops(fn) {
  subscribers.add(fn);
  arm();
  return () => {
    subscribers.delete(fn);
    if (!subscribers.size && timer) { clearInterval(timer); timer = null; }
  };
}

/** Whose desktop this is, in words: the routine's slug, or the conversation's / background
 *  task's with what it is — one wording for the dock's switcher and the page's cards. */
export function labelOf(d) {
  const o = d?.owner || {};
  if (o.kind === "routine") return o.slug;
  if (o.kind === "conversation") return `${o.slug} · conversation`;
  if (o.kind === "background") return `${o.slug} · background task`;
  return d?.name || "desktop";
}
