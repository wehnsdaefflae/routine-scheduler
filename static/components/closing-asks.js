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
    const { node } = answerForm(q, {
      submitText: (text, intermediate, decision) => api(`/api/questions/${ask.qid}/answer`,
        { method: "POST", body: decision ? { decision } : { text, intermediate } }),
      toastText: () => "answer sent — it reaches the next run",
    });
    box.append(el("div", { class: "closing-ask", "data-qid": ask.qid },
      el("div", { class: "prose" }, ask.question), node));
  }
  return box;
}
