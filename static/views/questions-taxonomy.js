// The Decisions inbox TAXONOMY: what the groups are, which filter chips exist, how the items
// are ordered, and what counts as urgent. Extracted from views/questions.js (R2425), which was
// at its size ceiling (tests/size_ratchet.json) — and because "what the inbox's categories ARE"
// is a different responsibility from rendering and answering a card.
//
// The page is an inbox sorted by URGENCY, and its groups all promised a CONSUMER waiting on the
// operator. PROPOSALS do not (R2425): a routine that files a standing idea — a config patch it
// noticed, a rename it would do — has no run parked on the answer, and "the next run picks these
// up" was literally false for every item of that shape. They render BELOW the groups that wait,
// with every urgency affordance off, because a proposal that has sat three weeks is not LATE.

import { TERMINAL } from "/static/states.js";

/** Is this decision's answer awaited by a CONSUMER — a parked run, a live run's next turn, a
 *  conversation that resumes, the self-audit report?
 *
 *  DERIVED, never declared: the question record carries no "I am a proposal" field and R2425
 *  settled that it must not grow one. The signal is the ASKING RUN's liveness, which
 *  /api/questions already carries: web/decisions_read.py links a deferred record to the run
 *  whose ts equals its `asked` and copies that run's `run_state` onto the row. A routine's
 *  deferred ask whose filing run has ENDED — or whose run dir is gone, so there is no
 *  `run_state` at all — has nobody waiting on it right now. It is the same derivation the
 *  card's own "answer & run now" button already makes, and that button exists for exactly
 *  these items: nothing consumes this answer until a run is fired.
 *
 *  What it does NOT capture: whether some FUTURE run will read the answer. That would need the
 *  asking routine's schedule, which this payload does not carry — so an ordinary deferred ask
 *  from a finished run reads as a proposal too. "Decide when you like" is still true of it;
 *  "the next run picks this up" was the claim that was not.
 */
export const waitsOnYou = (q) => {
  if (q.mode === "blocking") return true;            // a run is parked on it, right now
  if (q.meta) return true;                           // the self-audit report consumes it
  if (q.conversation || q.background) return true;   // read when that surface resumes
  return Boolean(q.run_state) && !TERMINAL.has(q.run_state);
};

/** The proof state a decision's options have reached, or "" — the client's reading of the map
 *  `rsched/proofs.py` stores on the record (D220). The RANK is that module's `_RANK`: a
 *  positive proof clears the row, so it outranks everything; `negative` is the loud case;
 *  `declined` says nothing about the decision, because nobody chose that option.
 *
 *  Derived here rather than served as a word, for the reason `waitsOnYou` is: the record
 *  carries the map and the map is the fact, so a second field saying what it means would be a
 *  copy to keep in sync. The order below is the only thing duplicated, and the engine test
 *  `tests/test_proofs.py` pins the same one.
 */
const PROOF_RANK = ["declined", "open", "in_progress", "negative", "positive"];
export const proofState = (q) => {
  const states = Object.values(q.proof || {})
    .map((e) => String(e?.state || "")).filter((s) => PROOF_RANK.includes(s));
  return states.length
    ? states.reduce((a, b) => (PROOF_RANK.indexOf(b) > PROOF_RANK.indexOf(a) ? b : a))
    : "";
};

/** Was this decision ANSWERED while nothing yet shows the work landed? (F795)
 *
 *  `rsched/proofs.unproven`, client-side. A row with no proof map is FALSE — every decision
 *  filed before D220 is in that state, and this band must not re-open the instance's whole
 *  settled history. His rule: a decision stays open and visible until one option has a
 *  positive proof, or it is actively discarded or overridden.
 */
export const unproven = (q) => Boolean(q.answered)
  && ["open", "in_progress", "negative"].includes(proofState(q));

export const FILTERS = [["all", "All"], ["blocking", "Blocking"], ["deferred", "Deferred"],
                        ["meta", "Meta"], ["proposal", "Proposals"], ["unproven", "Unproven"],
                        ["snoozed", "Snoozed"]];
export const SORTS = [["priority", "priority"], ["newest", "newest"], ["oldest", "oldest"],
                      ["routine", "routine"]];

/** The one filter bucket a decision belongs to — so a proposal is filterable on its own
 *  instead of swelling Deferred's count with items nothing is waiting for. */
export const kindOf = (q) => (unproven(q) ? "unproven"
  : q.meta ? "meta"
  : q.mode !== "blocking" && !waitsOnYou(q) ? "proposal"
  : q.mode);

const PROPOSAL = 3;
export const rank = (q) => (unproven(q) ? 4
  : q.answered ? 5
  : q.mode === "blocking" ? 0
  : q.meta ? 2
  : waitsOnYou(q) ? 1
  : PROPOSAL);

// inbox groups, strongest first — the priority sort renders these as sections
// [noun, what the group is, membership, does it wait on a person]
export const GROUPS = [
  ["Blocking", "a run is waiting on you", (q) => !q.answered && q.mode === "blocking", true],
  ["Deferred", "the next run picks these up",
   (q) => !q.answered && !q.meta && q.mode !== "blocking" && waitsOnYou(q), true],
  ["Meta", "system-level decisions", (q) => !q.answered && q.meta, true],
  // Below everything that waits, above what is already answered: nobody is waiting, so it is
  // not urgent — but it is still OPEN, so it is not settled either. `waits: false` keeps the
  // console's one colour for a person being waited on off this count.
  ["Proposals", "considered ideas, decide when you like",
   (q) => !q.answered && q.mode !== "blocking" && !q.meta && !waitsOnYou(q), false],
  // D220/F795: ANSWERED, and nothing has yet shown the work landed. Above Settled because the
  // item is not finished, below everything that waits because it is not a question — his hard
  // constraint is that a row whose proof is unproven is never re-presented as an unanswered
  // one. `waits: false` keeps the console's "a person is waited on" colour off a count of
  // decisions he has already made.
  ["Unproven", "answered, and nothing yet shows the work landed", unproven, false],
  ["Settled", "what you answered, and what became of it",
   (q) => q.answered && !unproven(q), false],
];

export const EXPIRING_MS = 30 * 60 * 1000;   // a blocking ask this close to its timeout is LOUD
export const expiringSoon = (q) => q.mode === "blocking" && q.expires
  && new Date(q.expires).getTime() - Date.now() < EXPIRING_MS;

const byAsked = (a, b) => String(a.asked || "").localeCompare(String(b.asked || ""));
const byDeadline = (a, b) =>
  String(a.expires || "9999").localeCompare(String(b.expires || "9999")) || byAsked(a, b);

/** The priority order the page renders as sections: group first, then — inside a group that
 *  WAITS — soonest deadline, then oldest. A proposal is never ranked by lateness: inside its
 *  group the items sit by routine, so the oldest one is not presented as the most overdue. */
const byPriority = (a, b) => rank(a) - rank(b)
  || (rank(a) === PROPOSAL ? String(a.routine || "").localeCompare(String(b.routine || ""))
        || byAsked(a, b)
      : byDeadline(a, b));

/** Order a filtered list for one toolbar sort. Non-priority sorts render flat (no sections). */
export function sortQuestions(qs, sort) {
  if (sort === "priority") return [...qs].sort(byPriority);
  if (sort === "oldest") return [...qs].sort(byAsked);
  if (sort === "newest") return [...qs].sort((a, b) => byAsked(b, a));
  if (sort === "routine") {
    return [...qs].sort((a, b) =>
      a.routine.localeCompare(b.routine) || rank(a) - rank(b) || byAsked(a, b));
  }
  return [...qs];
}
