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
// The snapshot also carries the standing PROPOSALS (/api/pending-creations — a queued
// creation, a met goal, library drift): they wait on a person exactly as a question does,
// and the badge and the tab notifier counted none of them until 2026-10. They are a separate
// list because the Decisions page renders them in their own band; the one read fetches both,
// and a failed proposals read keeps the last list rather than blanking the count.
//
// What the listener skips is llm_task / llm_process and NOTHING else. Those fire several
// times a second while a run works and cannot touch a decision. `run_state` can: a run that
// asks a blocking question goes `waiting_user`, and that event is the only announcement the
// bus carries of a question being asked.

import { api } from "/static/api.js";

const MIN_MS = 3000;    // leading fetch, then at most one per window, plus one trailing
                        // (owed by any event that arrived after the read it joined began)
const SKIP = new Set(["llm_task", "llm_process"]);

// The snapshot every reader shares: the list, and WHEN it was asked for. The `at` stamp is
// load-bearing for the transcript's form reconciliation — a form opened after the fetch
// started must not be closed by an answer list that predates it.
let snapshot = null;
let inflight = null;
let cooldown = null, trailing = false;
const subscribers = new Set();

// WHAT THIS CONSOLE ITSELF ANSWERED, and when — the mirror of the `at` stamp above.
//
// An answer POST and a bus repaint race, and the read the repaint joins may have STARTED
// before the POST landed: its list then still says the question is open. The Decisions page
// paints that list over the card it had just settled, so a FRESH ENABLED answer button
// appears under the cursor a median 1.8 s after the click — and the operator, seeing an
// un-pressed button, presses it again. MEASURED: 60 doubled click pairs in the client-stamped
// UI traces, zero of them sub-200ms, i.e. none of them a slip of the hand (F780/F772).
//
// Focus could not solve it: the form DISABLES the control it was clicked with, and a disabled
// button loses focus, so by the time the repaint arrives nothing in the list holds focus and
// the typing deferral (views/questions.js) legitimately declines to fire.
//
// So the store refuses the resurrection for every reader at once — the page, the header
// badge, the tier-1 notifier and the run view's inline forms: a snapshot older than the
// answer cannot speak for that question. An entry is dropped as soon as a read that BEGAN
// after the answer reports on it, so the server stays the authority on everything else
// (a revision, an expiry, a deletion) and nothing is pinned shut by this map.
const answeredHere = new Map();        // qid -> { at, text }

/** Record that THIS console answered `qid` — called by the one answer form. An intermediate
 *  (ask-back) reply must NOT be recorded: that question stays open by design. */
export function markAnswered(qid, text = "") {
  if (qid) answeredHere.set(qid, { at: Date.now(), text });
}

function healed(items, at) {
  if (!answeredHere.size || !Array.isArray(items)) return items;
  return items.map((q) => {
    const mark = answeredHere.get(q.qid);
    if (!mark) return q;
    if (at > mark.at) { answeredHere.delete(q.qid); return q; }   // read after the answer: server speaks
    return q.answered ? q : { ...q, answered: true, answer: q.answer || mark.text };
  });
}

function fetchNow() {
  if (inflight) return inflight;
  const at = Date.now();
  inflight = Promise.all([
    api("/api/questions"),
    api("/api/pending-creations").catch(() => snapshot?.proposals ?? []),
  ])
    .then(([items, proposals]) => {
      snapshot = { items: healed(items, at), proposals, at };
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
  // A read already in flight was asked for BEFORE this event, so its answer may not carry what
  // the event announced. Join it, and owe the one trailing read the window ends with — without
  // it the change waited for the next event or the 30 s floor.
  if (inflight) trailing = true;
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
