// The CLOSING asks a finished run left behind, rendered ON its finish banner (D212 (b)).
//
// A `closing` question is one the run finished its work WITHOUT (engine/askclass.py — the
// operator's own axis: `class` says whether the run was stopped by the gap, `mode` whether its
// process waited). Its reader is the person reading what the run DID, with the answer one click
// away — not a card on a page he visits separately, where it sits beside the decisions that ARE
// holding work up. The engine selects them (engine/closingasks.py) and puts them on the finish
// event's payload as `closing_asks`; this renders them.
//
// It POSTS THROUGH THE ONE ANSWER ROUTE every other surface uses (`answerForm`, which owns the
// single-answer-in-flight lock, the shared store's `markAnswered`, the toast and the failure
// unlock). A second poster would be a second place for the answer shape to drift, which is
// exactly what `answerform.js`'s own header warns about — so there is no fetch in this file.
//
// A run with no closing asks has no `closing_asks` key and this renders nothing: the banner is
// unchanged for the overwhelming majority of runs, which is why the key is omitted rather than
// sent empty.

import { api } from "/static/api.js";
import { el } from "/static/util.js";
import { answerForm } from "/static/components/answerform.js";

/** The receipt for an answered closing ask: what you said, and a way to change it.
 *
 * Posts to the SAME `/revise` route the Decisions page uses (`web/api_questions.revise_question_
 * answer`), which rewrites an answer still queued in the inbox and re-queues one a run already
 * read, so the next run reads the amendment instead of the original. No second route and no
 * second answer shape: this surface only offers the affordance where the reader already is.
 */
function settled(qid, text) {
  const wrap = el("div", {});
  const receipt = (said) => {
    const revise = el("button", { class: "btn small",
      title: "change what you answered — a decision a run already read is re-queued so the "
             + "next one sees the amendment" }, "revise");
    revise.onclick = () => edit(said);
    wrap.replaceChildren(
      el("div", { class: "flow-note" }, el("span", {}, `“${said}” → it reaches the next run`)),
      el("div", { class: "row mt" }, revise));
  };
  const edit = (said) => {
    const box = el("textarea", { class: "answer-input", rows: "3",
                                 "data-persist": `revise-${qid}` });
    box.value = said;
    const save = el("button", { class: "btn small primary" }, "save revision");
    const cancel = el("button", { class: "btn small" }, "cancel");
    cancel.onclick = () => receipt(said);
    save.onclick = async () => {
      const next = box.value.trim();
      if (!next) return;
      // Locked for the whole round trip and re-enabled in a `finally` — the only place it
      // cannot be forgotten. `questions.js`' own revise button once re-enabled on the failure
      // path alone, so a revision that SAVED left its button dead.
      save.disabled = cancel.disabled = true;
      try {
        await api(`/api/questions/${qid}/revise`, { method: "POST", body: { text: next } });
        receipt(next);
      } finally {
        save.disabled = cancel.disabled = false;
      }
    };
    wrap.replaceChildren(box, el("div", { class: "row mt" }, save, cancel));
    box.focus();
  };
  receipt(text);
  return wrap;
}


/** The block of closing asks for one finish banner, or null when there are none.
 *
 * `asks` is the finish payload's `closing_asks`: `{qid, question, options?, default?}` each, in
 * ask order. Every ask renders its own form, because each is answered on its own — they were
 * asked together only in the sense that one run asked them.
 */
export function closingAsks(asks) {
  const rows = (asks || []).filter((a) => a && a.qid && a.question);
  if (!rows.length) return null;
  const box = el("div", { class: "closing-asks", "data-closing-asks": String(rows.length) });
  // Said once, above the forms: a reader has to know these did NOT hold the run up, or a block
  // of questions under a finished run reads as something that went wrong.
  box.append(el("div", { class: "muted small prose" },
    rows.length === 1
      ? "One question this run left you — it finished without the answer; your answer shapes "
        + "the next run."
      : `${rows.length} questions this run left you — it finished without the answers; they `
        + "shape the next run."));
  for (const ask of rows) {
    const q = { qid: ask.qid, question: ask.question,
                options: ask.options || [], default: ask.default || "" };
    // `submitText` is the ONE answer route (`questionPanel`'s own call): a `decision` only ever
    // comes from an access request's typed buttons, and an access request is BLOCKING by
    // subject (askclass.of), so it can never reach this surface — it is threaded anyway rather
    // than dropped, because a route this file narrows is a route that can drift from the others.
    const slot = el("div", {});          // holds the form, then the settled receipt
    const { node } = answerForm(q, {
      submitText: (text, intermediate, decision) => api(`/api/questions/${ask.qid}/answer`,
        { method: "POST", body: decision ? { decision } : { text, intermediate } }),
      toastText: () => "answer sent — it reaches the next run",
      // D212, the REVISABLE half of the operator's brief ("collected, presented, answered and
      // revisable in the run's final summary"): what you said stays on the banner and can still
      // be changed. The Decisions page has had this since F525; this surface had the answer box
      // and nothing after it, so an answer typed here was final the moment it posted — and
      // answering one of three closing asks and then wanting to extend it is the ordinary case,
      // not an error to be locked out of.
      onSuccess: (text) => slot.replaceChildren(settled(ask.qid, text)),
    });
    slot.append(node);
    box.append(el("div", { class: "closing-ask", "data-qid": ask.qid },
      el("div", { class: "prose" }, ask.question), slot));
  }
  return box;
}
