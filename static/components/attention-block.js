// The seven classes of thing that might need the operator's intervention, as a labelled block
// above the run's narrative (D289).
//
// HIS ANSWER, 2026-11-14: "Lead with the seven classes as a labelled block at the top,
// narrative below it". And his requirement from the same message, which decides what a row
// carries: "they must be self-contained. they cannot have me hunting for content in other
// documents … worst case scenario is content i need to know about being referenced only by an
// identifier." So every row shows its own `detail` — an ask's options, a met goal's evidence,
// a partial finish's summary — rather than an id pointing at one.
//
// ORDER IS THE SERVER'S, never re-sorted here: `readmodels/attention.py` returns his order
// (a)…(g) and sorting client-side would be a second judgement nobody asked for.
//
// EMPTY RENDERS NOTHING. A run with nothing for him must read exactly as it did before this
// block existed, so `attentionBlock([])` returns null and the caller appends nothing — the
// block is a lead, not a frame.

import { el } from "/static/util.js";

// His labels, by class id. The server sends the id; the words live here because they are
// WORDING, and the read model is the classification.
const LABELS = {
  a: "context to decide",
  b: "unexpected event",
  c: "communication",
  d: "reached goal",
  e: "block the system cannot resolve",
  f: "pivot",
  g: "dead end",
};

/** One row: its class label, what it is, and the content he would otherwise go and find. */
function row(r) {
  const times = r.occurrences > 1 ? ` ×${r.occurrences}` : "";
  return el("div", { class: "att-row", "data-att-class": r.class },
    el("span", { class: "att-label" }, `${r.class} ${LABELS[r.class] || r.class}`),
    el("span", { class: "att-text" }, `${r.text || ""}${times}`),
    r.detail ? el("div", { class: "att-detail faint small" }, r.detail) : null);
}

/** The block, or null when there is nothing he needs to see. */
export function attentionBlock(rows) {
  if (!Array.isArray(rows) || !rows.length) return null;
  return el("div", { class: "att-block", "data-attention": "" }, ...rows.map(row));
}
