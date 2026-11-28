// A decision card's QUESTION BODY, with a height bound on the open card.
//
// The page's stated priority is blocking > closing > meta > proposals > settled, and its
// rendering inverted it: a SETTLED card collapses its body to one summary line (right, for a
// receipt), while an OPEN card rendered the full markdown inline with no height bound at all —
// no max-height, no line-clamp, no scroll. Since the longest bodies on the page are proposals,
// the group that nothing is waiting on, the lowest-priority items took the most vertical weight
// and pushed every card's own answer form below the fold. Measured on this instance: a settled
// archive of 26 records at 228,512 B, the newest proposal 11,742 B.
//
// So the bound is on the OPEN card, and three things follow from what the page is FOR:
//
//   * A BLOCKING question is never clamped. A run is parked waiting on it, so reading all of it
//     IS the page's business — that group is the one place unbounded height is correct.
//   * The clamp keeps the body BLOCK-rendered. A run lays out what it found (a table, a list, a
//     fenced snippet) before it asks, and md.js's `clampedBody` renders inline, which turned a
//     table into literal pipes in the one place the user must read carefully. Hence a height
//     bound rather than that helper's two-line clamp.
//   * It is a HEIGHT, not a character count, and a readable one: enough for the title plus the
//     first claim, so the reader can decide whether to read on. Four lines shows only the title
//     and makes every card a click.
//
// The toggle is only built when the body actually overflows, measured after layout — a short
// question gets no control, the way `clampedBody` adds none below its threshold.

import { md } from "/static/md.js";
import { linkifyRefs } from "/static/components/reflinks.js";
import { el } from "/static/util.js";

//: The open card's body height, in lines of its own prose (views.css `.question-item .q-text`
//: sets line-height 1.55). Twelve shows a proposal's title and its first claim.
export const CLAMP_LINES = 12;

/** One decision's question, rendered.
 *
 *  The text is MARKDOWN, block-rendered whoever wrote it: a run lays out what it found (a GFM
 *  table, a numbered list, a fenced snippet) before it asks, and inline those became literal
 *  pipes in the one place the user must read carefully. Finding/decision ids (F63, D14) link
 *  only in the audit's own voice (meta items); elsewhere a bare "D1" is a false positive.
 *
 *  `clamp` is the OPEN card: a height bound and, when the body overflows it, a "read all"
 *  toggle. The settled card passes nothing — its own summary already collapses the body.
 */
export function questionText(q, { clamp = false } = {}) {
  const node = el("div", { class: "q-text" }, md(q.question));
  const body = q.meta ? linkifyRefs(node) : node;
  return clamp ? clampQuestionBody(q, body) : body;
}

/** Bound `node`'s height unless this card is one a run is waiting on.
 *
 *  Returns the node to place in the card. When the body overflows the bound, a "read all"
 *  toggle is appended inside it — inside rather than beside, so a host that places the body
 *  anywhere gets the control with it and no caller has to know the clamp exists.
 */
export function clampQuestionBody(q, node) {
  if (q && q.mode === "blocking") return node;
  node.classList.add("q-clamped");
  const toggle = el("button", { class: "btn small ghost q-readall", type: "button",
    "data-question-readall": "" }, "read all");
  toggle.onclick = () => {
    toggle.textContent = node.classList.toggle("q-clamped") ? "read all" : "collapse";
  };
  // The decision needs LAYOUT: whether this body overflows twelve lines is not knowable from
  // its text, which is markdown whose rendered height depends on the viewport. One frame after
  // insertion the host has placed it, so scrollHeight is real; before that it is 0 and every
  // card would get a toggle it does not need.
  requestAnimationFrame(() => {
    if (node.scrollHeight > node.clientHeight + 1) node.append(toggle);
    else node.classList.remove("q-clamped");
  });
  return node;
}
