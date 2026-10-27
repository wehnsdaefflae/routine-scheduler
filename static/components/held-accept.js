// The HELD ACCEPT banner: an accept pressed while a run was active is waiting, and says when
// it lands (D178).
//
// The accept itself is still refused — a multi-field accept during a live run would leave the
// routine with neither its old settings nor its new ones. What was missing is the rest of the
// answer. On 2026-10-11 the operator pressed this page's accept three times in one second while
// a run was 78 minutes from ending; each press toasted "try again after it ends", which names no
// time and survives six seconds, and the next day's traces show he never tried again.
//
// So the refusal now leaves something on the page: these changes are waiting, they apply when
// the run ends, and that is expected by <time>. It is written as a SENTENCE a reader scans —
// the `.held-accept` hook exists for the tests, not as the place the meaning lives.
//
// It wears `.draft-banner` as well: the pending-proposal banner's styling is exactly right for
// "something waits on this page", and both stylesheets are at their size ratchet (the house
// answer is a new file, and a second copy of a banner's look is not worth one).

import { el, when } from "/static/util.js";

/** The one sentence this banner is for — and, since F761, the ONLY place it is said. The
 *  refusal toast carried the same words through the console's ERROR channel, where a 409 reads
 *  as a failed click; the words were never the defect, the channel was. */
export function heldLine(held) {
  const n = Object.keys(held?.changes || {}).length;
  const count = `${n} change${n === 1 ? "" : "s"}`;
  const words = held?.expected_end_words || "";
  if (held?.expired) {
    const moved = held.expired.length ? held.expired.join(", ") : "";
    return moved
      ? `${count} did not apply: ${moved} was decided elsewhere while the run held your accept.`
      : `${count} did not apply: ${held.error || "the accept could not be applied at run end"}.`;
  }
  return words
    ? `${count} waiting — your accept applies when the run ends, expected by ${words}.`
    : `${count} waiting — your accept applies when the run ends (this routine's runs have no `
      + "wall-clock limit).";
}

/** heldAccept(form) → node. Repaints from the form's payload, so every re-read moves it. */
export function heldAccept(form) {
  const node = el("div", { class: "draft-banner held-accept", "data-held-accept": "",
    role: "status", hidden: true });

  function paint() {
    const held = form.payload?.held_accept || null;
    node.hidden = !held;
    if (!held) { node.replaceChildren(); return; }
    node.replaceChildren(...[
      el("div", { class: "db-message" }, heldLine(held)),
      el("div", { class: "db-how small" }, held.expired
        ? "Nothing was written: an accept applies whole or not at all, and the older click does "
          + "not overwrite a newer decision. The values below are still yours to accept — press "
          + "accept changes again when you want them."
        : "You need not click again. The changes stay exactly as you left them, and the page "
          + "shows them below until they land."),
      held.labels?.length
        ? el("div", { class: "db-meta" }, held.labels.join(" · ")) : null,
      el("div", { class: "db-meta" },
        held.run_id ? el("code", {}, held.run_id) : null,
        held.queued ? el("span", {}, " · pressed ") : null,
        held.queued ? when(held.queued) : null),
    ].filter(Boolean));
  }

  paint();
  form.subscribe(paint);
  return node;
}
