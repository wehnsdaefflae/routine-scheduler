// ONE reader of /api/questions for the whole console.
//
// Four surfaces want the same list — the header badge, the tier-1 notifier, the Decisions
// page and the run view's inline answer forms — and each used to fetch it on its own bus
// listener with its own throttle (3 s / 5 s / none / once at boot). One run_finished with the
// Decisions page open and notifications on therefore produced three GETs of the same list
// inside a second, and the daemon's load depended on which tab happened to be open rather
// than on what had changed — on an endpoint that is one of the two read models that starved
// it (0.340.0: three catalog walks per call, 10-20 in flight).
//
// So: one bus listener, one cadence, one in-flight fetch, many subscribers.
//
// What the listener skips is llm_task / llm_process and NOTHING else. Those fire several
// times a second while a run works and cannot touch a decision. `run_state` can: a run that
// asks a blocking question goes `waiting_user`, and that event is the only announcement the
// bus carries of a question being asked.

import { api } from "/static/api.js";

const MIN_MS = 3000;    // leading fetch, then at most one per window, plus one trailing
const SKIP = new Set(["llm_task", "llm_process"]);

// The snapshot every reader shares: the list, and WHEN it was asked for. The `at` stamp is
// load-bearing for the transcript's form reconciliation — a form opened after the fetch
// started must not be closed by an answer list that predates it.
let snapshot = null;
let inflight = null;
let cooldown = null, trailing = false;
const subscribers = new Set();

function fetchNow() {
  if (inflight) return inflight;
  const at = Date.now();
  inflight = api("/api/questions")
    .then((items) => {
      snapshot = { items, at };
      for (const fn of [...subscribers]) {
        try { fn(snapshot); } catch { /* one bad reader must not starve the others */ }
      }
      return snapshot;
    })
    .finally(() => { inflight = null; });
  return inflight;
}

/** Fetch now (joining a fetch already in flight). REJECTS on failure, because the reader
 *  that asked explicitly is the one that can say so on the page; the bus path below cannot,
 *  and swallows. */
export function loadQuestions() { return fetchNow(); }

/** Register a reader; returns its unsubscribe. Deliberately does NOT deliver the current
 *  snapshot: every reader does its own first load through loadQuestions(), which joins a
 *  fetch already in flight, and a subscription that also painted would render each view twice
 *  at mount. Subscribing is for what happens NEXT. */
export function subscribeQuestions(fn) {
  subscribers.add(fn);
  return () => subscribers.delete(fn);
}

function schedule() {
  if (cooldown) { trailing = true; return; }
  fetchNow().catch(() => { /* the daemon lamp reports the link; the last list stands */ });
  cooldown = setTimeout(() => {
    cooldown = null;
    if (trailing) { trailing = false; schedule(); }
  }, MIN_MS);
}

window.addEventListener("rsched-bus", (e) => {
  if (!subscribers.size) return;          // nobody is looking — no request exists to make
  if (SKIP.has(e.detail?.event)) return;
  schedule();
});
