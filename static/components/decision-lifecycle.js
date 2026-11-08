// The decision card's LIFECYCLE controls — everything that disposes of an open decision
// without answering it in prose. Three verbs, one row, beside the answer form:
//
//   defer to next run   a blocking question only: unblock the run on its stated default,
//                       the record stays OPEN (POST /questions/{qid}/defer)
//   snooze / unsnooze   hide it here until a timestamp — UI noise control, NOT an answer:
//                       runs still see it as open (POST /questions/{qid}/snooze)
//   decline             the first-class NO (R2197): the item leaves the open set as
//                       DECLINED, and the asking routine reads `decision: "declined"` as a
//                       value (POST /questions/{qid}/decline)
//
// Extracted from views/questions.js (R2197), which was at its size ceiling
// (tests/size_ratchet.json): the new verb had no room there, and these three belong
// together — each ends the card's life on the page a different way, and none of them is the
// answer protocol the form owns.
//
// The host builds this BEFORE its panel and controls exist (the answer form takes these
// nodes as `extraControls`), so every reference back into the card goes through a callback
// that runs at CLICK time rather than through a node captured at build time.

import { api } from "/static/api.js";
import { chip, el, toast, toastError } from "/static/util.js";
import { proofState } from "/static/views/questions-taxonomy.js";

const SNOOZES = [["60", "1 hour"], ["240", "4 hours"], ["1440", "1 day"], ["10080", "1 week"]];
//: The one decision VALUE a refusal carries, on the API row and in the answer file alike
//: (rsched/decline.py owns it server-side).
const DECLINED = "declined";

/** The lifecycle nodes for one decision card, as an array to spread into `extraControls`.
 *
 *  `settle(label, note)` — the host's "this card is done" rendering (drop the warn class,
 *  replace the controls with a flow note). `refresh()` repaints the list. `syncToolbar()`
 *  re-counts the filter chips. `reason()` supplies the operator's optional decline reason —
 *  whatever he has already typed in the answer box, so declining stays ONE click.
 */
export function lifecycleControls(q, { settle, refresh, syncToolbar, reason } = {}) {
  if (q.meta) return [];              // an audit decision is the report's, not a record
  return [declineButton(q, { settle, syncToolbar, reason }),
          q.mode === "blocking" ? deferButton(q, { settle, syncToolbar })
                                : snoozeControl(q, { refresh })].filter(Boolean);
}

/** The NO. Posts the decline, which files the ordinary answer payload with
 *  `decision: "declined"` — so the item leaves the open set as declined rather than as
 *  prose, and the routine that asked matches a token instead of parsing a sentence.
 *
 *  NOT RENDERED AT ALL for an access request: that record's refusal is the typed decision
 *  `deny_now` / `deny_forever` (the answer form's own buttons), which is what the engine
 *  reads to deny the grant — the endpoint refuses it with a 400, so offering a dead control
 *  would be worse than offering none. Absent from the DOM, not merely hidden.
 */
function declineButton(q, { settle, syncToolbar, reason }) {
  if (q.type === "request") return null;
  const btn = el("button", { class: "btn small danger", "data-decline": "",
    title: "say NO: the decision leaves the open set as DECLINED and the routine that asked "
      + "reads it as a refusal — not as prose. Anything typed in the answer box rides along "
      + "as your reason" }, "decline");
  btn.onclick = async () => {
    btn.disabled = true;
    try {
      const why = (reason?.() || "").trim();
      const r = await api(`/api/questions/${q.qid}/decline`,
        { method: "POST", body: { reason: why } });
      toast("declined — the routine that asked reads it as a refusal");
      // Mark it in place rather than waiting for a reload: a deferred question's pending file
      // is only consumed when its routine next runs, so the card is still listed and a reader
      // would otherwise see his refusal as still-open ("didn't work").
      q.answered = true;
      q.decision = r.decision;
      q.reason = why;
      q.answer = r.text;
      // `failed` (red), the same class the settled receipt's chip uses — a refusal that looks
      // amber here and red after a reload reads as two different outcomes.
      settle?.("declined", "the routine that asked reads this as a refusal, not as prose",
               "failed");
      syncToolbar?.();
    } catch (err) { toastError(err); btn.disabled = false; }
  };
  return btn;
}

/** Unblock a parked run WITHOUT deciding — it continues on its stated default and the
 *  question stays open for the next run. Blocking records only. */
function deferButton(q, { settle, syncToolbar }) {
  const btn = el("button", { class: "btn small",
    title: "unblock the run WITHOUT deciding — it continues on its stated default and the "
      + "question stays open for the next run" }, "defer to next run");
  btn.onclick = async () => {
    btn.disabled = true;
    try {
      await api(`/api/questions/${q.qid}/defer`, { method: "POST", body: {} });
      toast("deferred — the run continues on its default");
      settle?.("deferred to next run",
               "the run continues on its default — the question stays open");
      q.mode = "deferred";
      syncToolbar?.();
    } catch (err) { toastError(err); btn.disabled = false; }
  };
  return btn;
}

/** Hide it here for a while (or bring it back) — the routine still sees it as open. */
function snoozeControl(q, { refresh }) {
  if (q.snoozed) {
    const btn = el("button", { class: "btn small", title: "bring it back into the inbox now" },
      "unsnooze");
    btn.onclick = async () => {
      try {
        await api(`/api/questions/${q.qid}/snooze`, { method: "POST", body: { minutes: 0 } });
        toast("back in the inbox");
        q.snoozed = false;
        delete q.snoozed_until;
        refresh?.();
      } catch (err) { toastError(err); }
    };
    return btn;
  }
  const sel = el("select", { class: "small", "data-nopersist": true,
    title: "hide this decision here for a while — the routine still sees it as open" },
    el("option", { value: "" }, "snooze…"),
    ...SNOOZES.map(([min, label]) => el("option", { value: min }, label)));
  sel.onchange = async () => {
    if (!sel.value) return;
    try {
      const r = await api(`/api/questions/${q.qid}/snooze`,
        { method: "POST", body: { minutes: +sel.value } });
      toast("snoozed — it waits under the Snoozed filter");
      q.snoozed = true;
      q.snoozed_until = r.snoozed_until;
      refresh?.();
    } catch (err) { toastError(err); sel.value = ""; }
  };
  return sel;
}

/** The settled card's verdict chip: a declined decision says DECLINED, in red, instead of
 *  "answered · queued" — a refusal that reads as an answer is the thing R2197 set out to fix.
 *  Returns null for an ordinary answer, so the host keeps its own chip.
 *
 *  Reads the `decision` VALUE and no separate flag: that one key is what the API row carries
 *  whether the answer is still queued (derived from the inbox file) or already consumed (read
 *  from the durable archive), so the chip cannot appear on one path and vanish on the other. */
export function declinedChip(q) {
  return q.decision === DECLINED
    ? chip(`declined${q.reason ? ` · ${q.reason}` : ""}`, "failed")
    : null;
}

/** What this decision's PROOF says, or null — the chip on an answered-but-unproven card (D220).
 *
 *  `negative` is the only loud one, and it is the one thing this page shouts about a decision
 *  already answered: the actions were performed and the proof is STILL absent, which is the
 *  operator's own "unexpected event" state. `in_progress` is the ordinary waiting case and
 *  wears the muted treatment, because nothing is wrong yet — the work simply has not shown
 *  up. A `positive` proof is not chipped at all: the row has left this band for Settled, where
 *  an answered decision belongs, and a chip saying so would be noise on every settled card the
 *  instance ever shows.
 *
 *  Reads the stored map through the taxonomy's `proofState`, the same reduction
 *  `rsched/proofs.state_of` makes server-side, so the chip and the band can never disagree
 *  about which state a row is in. */
export function proofChip(q) {
  const state = proofState(q);
  if (state === "negative") {
    return chip("proof absent — the work was authorized and has not shown up", "failed");
  }
  return state === "in_progress" ? chip("proof pending", "waiting") : null;
}

/** What is being CHECKED, as the card's own line — or [] so a spread adds nothing.
 *
 *  An array rather than a node-or-null because `replaceChildren` stringifies a null argument
 *  into the text "null" (only `el()` drops them), which is the defect the Messages page's queue
 *  row carries a comment about. */
export function proofNote(q) {
  const entry = Object.values(q.proof || {}).find(
    (e) => e?.state === "in_progress" || e?.state === "negative");
  return entry ? [el("div", { class: "small faint" },
    `proof: \`${entry.predicate}\` over ${entry.reads} is what shows this landed`)] : [];
}

/** Every chip an ANSWERED card's meta row carries: what the answer WAS, and what became of it.
 *
 *  One function because there are now three overlapping facts and the host was composing them
 *  inline in a file that has no room (`views/questions.js` is frozen at its ratchet ceiling):
 *  a DECLINE replaces the answered chip (R2197 — a refusal rendered as an ordinary answer is
 *  what the decline verb exists to end), while a PROOF rides BESIDE it (D220 — the answer
 *  stands; whether the work it authorized landed is a second fact, and `negative` is the one
 *  thing this page shouts about a decision already made). */
export function answeredChips(q) {
  const via = q.answer_source && q.answer_source !== "web" ? ` via ${q.answer_source}` : "";
  const fate = q.settled ? "acted on" : q.ran_now ? "run started" : "queued";
  return [declinedChip(q) || chip(`answered${via} · ${fate}`, "ok"), proofChip(q)];
}
