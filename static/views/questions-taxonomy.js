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
//
// The groups split BY CLASS, not by whose process is parked — D212 increment (c), the operator's
// *"Both axes: subject decides the default, the run may override it when the work is genuinely
// blocked"*. `class` (engine/askclass.py) answers "could the run do the work it was fired to do
// without this?"; `mode` only "did its process sit and wait?". Grouping by `mode` made the band
// that promised "a run is waiting on you" structurally empty — measured over the 282 records
// this instance had filed, `mode: "blocking"` was used 0 times — while a run that said
// `blocks_work: true` and then ENDED was demoted all the way to Proposals, the band whose own
// caption says nobody is waiting on it. So the two bands at the top are Blocking and Closing,
// and what is left of the old Deferred/Proposals pair is the consumer question `waitsOnYou`
// already answers.

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

/** The CLASS this decision carries — `"blocking"` or `"closing"` (D212 (c)).
 *
 *  `engine/askclass.of`, client-side, and the same three-step reading `engine/closingasks.of_record`
 *  makes server-side — stated once here because the bands, the filter chips, the rank and the
 *  captions must all read one derivation or the toolbar and the page can disagree about a card:
 *
 *  1. a record whose asking run is PARKED on it (`mode === "blocking"`) is blocking whatever the
 *     key says — the run demonstrably could not go on without it;
 *  2. otherwise the recorded `class`, which is the asking run's own claim (increment (a));
 *  3. otherwise the SUBJECT default, for every record filed before the key existed: a typed
 *     access `request` means the run cannot reach the thing at all, so it is blocking; anything
 *     else is closing. That is `askclass.of`'s rule verbatim, and the conservative direction —
 *     an unknown value reads as closing rather than shouting.
 */
export const classOf = (q) => {
  if (q.mode === "blocking") return "blocking";
  if (q.class === "blocking" || q.class === "closing") return q.class;
  return Array.isArray(q.request) && q.request.length ? "blocking" : "closing";
};
export const isBlocking = (q) => classOf(q) === "blocking";

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

/** Answered, and NO run will ever read the answer (`web/decision_pickup`, 2026-11-09): its
 *  conversation finished during the turn it was filed in, or its routine is off or has no
 *  clock. It waits on the operator again — deliver it to a run, or settle it without one. */
export const undelivered = (q) => Boolean(q.answered) && !q.settled
  && q.pickup?.state === "never";

export const FILTERS = [["all", "All"], ["blocking", "Blocking"], ["closing", "Closing"],
                        ["meta", "Meta"], ["proposal", "Proposals"], ["undelivered", "Undelivered"],
                        ["unproven", "Unproven"],
                        ["snoozed", "Snoozed"]];
export const SORTS = [["priority", "priority"], ["newest", "newest"], ["oldest", "oldest"],
                      ["routine", "routine"]];

/** The one filter bucket a decision belongs to — so a proposal is filterable on its own
 *  instead of swelling Closing's count with items nothing is waiting for. The two question
 *  buckets are the CLASS (D212 (c)); `proposal` stays what R2425 made it, a CLOSING ask no
 *  consumer is waiting on at all. */
export const kindOf = (q) => (undelivered(q) ? "undelivered" : unproven(q) ? "unproven"
  : q.meta ? "meta"
  : !isBlocking(q) && !waitsOnYou(q) ? "proposal"
  : classOf(q));

const PROPOSAL = 3;
export const rank = (q) => (undelivered(q) ? 1 : unproven(q) ? 4
  : q.answered ? 5
  : isBlocking(q) ? 0
  : q.meta ? 2
  : waitsOnYou(q) ? 1
  : PROPOSAL);

// inbox groups, strongest first — the priority sort renders these as sections
// [noun, what the group is, membership, does it wait on a person]
export const GROUPS = [
  // D212 (c): the CLASS, not the parked process. The captions state the class's own test, because
  // "a run is waiting on you" was false of the band in both directions — a parked run is one way
  // to be blocked, not the definition, and nothing waits on a run that already finished.
  ["Blocking", "a run could not do its job without your answer",
   (q) => !q.answered && isBlocking(q), true],
  ["Closing", "the run finished without it — your answer shapes the next one",
   (q) => !q.answered && !q.meta && !isBlocking(q) && waitsOnYou(q), true],
  ["Meta", "system-level decisions", (q) => !q.answered && q.meta, true],
  // ANSWERED, but nothing will read the answer: it is the operator's again, so it WAITS.
  ["Undelivered", "you answered, but no run will ever read it — deliver it or settle it",
   undelivered, true],
  // Below everything that waits, above what is already answered: nobody is waiting, so it is
  // not urgent — but it is still OPEN, so it is not settled either. `waits: false` keeps the
  // console's one colour for a person being waited on off this count.
  ["Proposals", "considered ideas, decide when you like",
   (q) => !q.answered && !isBlocking(q) && !q.meta && !waitsOnYou(q), false],
  // D220/F795: ANSWERED, and nothing has yet shown the work landed. Above Settled because the
  // item is not finished, below everything that waits because it is not a question — his hard
  // constraint is that a row whose proof is unproven is never re-presented as an unanswered
  // one. `waits: false` keeps the console's "a person is waited on" colour off a count of
  // decisions he has already made.
  ["Unproven", "answered, and nothing yet shows the work landed",
   (q) => unproven(q) && !undelivered(q), false],
  ["Settled", "what you answered, and what became of it",
   (q) => q.answered && !unproven(q) && !undelivered(q), false],
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
