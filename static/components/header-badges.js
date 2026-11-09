// The rail's two counts of WORK WAITING ON A PERSON — decisions, and blocks only he can clear.
//
// Both badges say the same kind of thing, which is why one module owns them: the palette
// reserves `--summons` for "waiting on you" and `.badge` is that colour, so a count in the rail
// means a person is the next mover. Nothing merely alive wears one (base.css's own header says
// so). They differ only in their source — the shared questions store, and the blocked-work fold.
//
// WHY THE SECOND ONE EXISTS (F796). `GET /api/health/blocked` folds nineteen events that mean
// something DUE did not happen, and until now its only reader was one panel on the Stats page,
// inside a 7-day window. That is the right home for the fleet's whole blocked list — but a few
// of those rows are blocks NOTHING INSIDE THE SYSTEM WILL CLEAR: a stale backup needs a host
// unit started, a pause is dropping due fires that are never made up (D237), a one-shot was
// consumed unfired, a lane names a member that is not a routine. Those wait for the operator,
// and waiting for him on a page he has no reason to open is the same as not telling him:
// `backup_stale` ran nineteen unnoticed nights, and a pause that ate eight due fires was found
// by an audit reading the ABSENCE of run directories.
//
// The judgement of which rows those are is the SERVER's (`readmodels/operator_blocks.py`), as
// `vocabulary` already is: this module renders `operator_total` and the rows' own
// `waiting_on_you` sentences, and is compiled with no event name at all. So adding an event to
// that set lights this badge with no console change.
//
// The badge counts ROWS, not occurrences — one per thing to go and fix — because that is the
// number a reader can act on: eight dropped fires of one routine are one unpause.

import { api } from "/static/api.js";
import { subscribeQuestions } from "/static/questions-store.js";

// How often the blocked count is re-read. These events are filed by daily watchers and by the
// scheduler's own tick, so a minute is far finer than the thing it measures; the fold is
// memoized on the stream's fingerprint, so a re-read costs nothing while the file stands still.
const BLOCKED_MS = 60000;

function paint(id, count, title) {
  const badge = document.getElementById(id);
  if (!badge) return;
  badge.textContent = count;
  badge.title = title;
  badge.hidden = count === 0;
}

// One READER of the shared questions store (questions-store.js), which owns the bus listener,
// the cadence and the single in-flight fetch for every surface reading /api/questions. It counts
// everything waiting on a person: the open questions and the standing proposals the Decisions
// page shows in its own band.
function paintDecisions({ items, proposals = [] }) {
  // answered-but-unconsumed items are settled; snoozed ones wait silently by design
  const open = items.filter((q) => !q.answered && !q.snoozed).length + proposals.length;
  paint("q-badge", open, open ? `${open} waiting on you` : "");
}

/** The operator's share of the blocked-work fold, or nothing if it cannot be read (a badge is a
 * reading of the fleet, never a reason for the page to fail — the daemon lamp owns connectivity).
 */
async function paintBlocked() {
  let d;
  try {
    d = await api("/api/health/blocked");
  } catch {
    return;
  }
  const n = d.operator_total | 0;
  // the rows' OWN sentences, deduplicated: two routines' dropped fires are one thing to do
  const says = [...new Set((d.rows || []).filter((r) => r.operator)
    .map((r) => r.waiting_on_you).filter(Boolean))];
  paint("blocked-badge", n, n
    ? `${n} ${n === 1 ? "block" : "blocks"} only you can clear:\n· ${says.join("\n· ")}`
    : "");
}

/** Wire both badges: the decisions count through the store, the blocked count on its own floor.
 * Called once, by the entry module.
 */
export function initHeaderBadges() {
  subscribeQuestions(paintDecisions);
  paintBlocked();
  setInterval(paintBlocked, BLOCKED_MS);
  // A watcher files its event off the scheduler's tick, not inside a run, so there is no bus
  // event for "a block appeared" to listen for. A reconnect, though, means this tab may have
  // been asleep while one did: re-read then rather than waiting out the floor.
  window.addEventListener("rsched-bus", (ev) => {
    if (ev.detail?.event === "reconnect") paintBlocked();
  });
}
