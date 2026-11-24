// The SETTLED RECEIPT — one answered decision's card on the Decisions page.
//
// Extracted from `static/views/questions.js`, which was at its size ceiling, and extracted along
// this seam rather than wherever there was room: a settled card is a RECEIPT and an open card is
// an ASK. They share only the meta row (which the caller passes in, because it builds the same
// row for both shapes); everything else is different work. The receipt answers "what did I say,
// and what became of it", offers the one affordance that still applies (revise), and keeps a
// config proposal alive that answering did not apply. The open card owns the answer protocol —
// the form, the lifecycle controls, "answer & run now" — and none of that is here.
//
// WHAT THE RECEIPT EXISTS FOR (F525). Before the durable record, consuming an answer deleted it:
// you answered, a run booted, and your own words were gone from the only surface that had shown
// them. A settled decision keeps its card, and stays REVISABLE — answering one of four questions
// and wanting to add the other three is an ordinary thing to do, not an error to be locked out
// of. The revise path posts to `POST /api/questions/{qid}/revise`, which rewrites an answer still
// queued in the inbox and re-queues one a run already read, so the next run reads the amendment.
//
// AND WHY THE PROPOSAL IS STILL OFFERED HERE (R2189, the operator's ruling: "only the button;
// answering warns and keeps the patch available"). Answering NEVER applies a `config_patch`, so a
// settled card whose patch is unapplied must keep offering it — without that, the proposal and
// its only control vanished the moment you answered. Reported twice, the second time on an option
// that read "Ja — Patch übernehmen", where the answer said yes and nothing happened.

import { api } from "/static/api.js";
import { act, el } from "/static/util.js";
import { proofNote } from "/static/components/decision-lifecycle.js";

/** The receipt card for an answered decision `q`, as a panel node.
 *
 * `meta` is the card's meta row, built by the caller (the open card builds the same row, and a
 * second construction of it is a second place for the chips to drift).
 * `body` is the question's rendered prose, likewise the caller's: the page owns how a question
 * is rendered, including its markdown and its summary line.
 * `proposal` is the config-bridge factory (`components/configproposal.js`, wrapped by the view
 * so an applied patch drops the row from its own list).
 */
export function settledCard(q, { meta, question, proposal }) {
  const body = el("div", {});
  const settledNote = () => (q.settled
    ? "→ read by the run that asked"
    : `→ inbox → consumed by the ${q.mode === "blocking" ? "waiting run"
        : q.ran_now ? "run starting now" : "next run"}`);

  const reviseBtn = el("button", { class: "btn small",
    title: "change what you answered — a decision a run already read is re-queued so the next "
           + "one sees the amendment" }, "revise");

  const say = (text, note) => body.replaceChildren(
    el("div", { class: "flow-note" }, el("span", {}, `“${text}” ${note}`)),
    ...proofNote(q),                 // D220: what is being CHECKED, so the band reads
    el("div", { class: "row mt" }, reviseBtn));

  const editor = () => {
    const box = el("textarea", { class: "answer-input", rows: "3",
                                 "data-persist": `revise-${q.qid}` });
    box.value = q.answer || "";
    const save = el("button", { class: "btn small primary" }, "save revision");
    const cancel = el("button", { class: "btn small" }, "cancel");
    cancel.onclick = () => say(q.answer, settledNote());
    save.onclick = () => {
      const text = box.value.trim();
      if (!text) return;
      // act() re-enables in a `finally`, which is the only place it cannot be forgotten: this
      // handler used to re-enable only on the failure path, so a revision that SAVED left its
      // own button dead.
      act(save, async () => {
        const r = await api(`/api/questions/${q.qid}/revise`,
                            { method: "POST", body: { text } });
        q.answer = text;
        q.settled = false;              // re-queued: a run has to read the amendment
        say(text, "→ inbox → the next run reads this instead");
        return r;
      }, "revised — the next run reads the new answer");
    };
    body.replaceChildren(box, el("div", { class: "row mt" }, save, cancel));
    box.focus();
  };
  reviseBtn.onclick = editor;
  say(q.answer, settledNote());

  // R2778: the proposal's host is this WRAPPER, not the card body, so applying REPLACES the
  // "NOT applied" warning instead of landing beside it — with the body as the host, one card
  // carried both labels at once and no reader could tell which of them was true.
  const settledConfig = el("div", { class: "mt" });
  const settledBar = proposal(q, { host: settledConfig, clearWarn: () => {} });
  if (settledBar) {
    settledConfig.append(
      el("div", { class: "small faint" },
        "the proposed config change was NOT applied — answering never applies it; "
        + "approve it here while it is still offered"), settledBar);
  }

  return el("div", { class: "panel question-item answered" },
    meta,
    // A settled card is a RECEIPT: what was asked, what you said, and the chance to revise.
    // Rendering the whole proposal body — rationale, prior art, sketch, an impact table — above
    // the answer made the page eleven thousand pixels of decisions already taken. The first
    // line leads; the rest is one click, and nothing is dropped.
    question,
    el("div", { class: "mt" }, body),
    settledBar ? settledConfig : null);
}
