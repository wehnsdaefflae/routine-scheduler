// Item reference tokens (F63, D14, R7) in rendered prose become links to the card they name
// on the Messages page (#/messages?focus=F63) — clicking one anywhere in the console lands on,
// scrolls to, and flashes that card. linkifyRefs walks TEXT nodes only, so it composes
// with mdInline output and never rewrites existing links, code, or form controls. Apply it
// ONLY to prose that is the maintenance record's own voice (the Messages page, meta-badged
// decisions) — on arbitrary text a bare "D1" is a false positive. `R` and not `B` for bug
// reports: the user's own reviewer-backlog items are written B<n> and would mislink.

import { flash } from "/static/landing.js";
import { loadRefTitles, onTitles, titleOf, titlesLoaded } from "/static/ref-titles.js";

// Unbounded digits: the R namespace passes R10000, and a capped pattern left every id past it
// as plain text. The optional LETTER SUFFIX is a real part of the namespace, not a courtesy:
// `F557b` is in the ledger today, and a run names a follow-on increment `D307b` while it builds
// it — without the suffix the pattern matched `D307` and left a stray `b` outside the link,
// which both mislinks and reads as a typo (the operator, 2026-11-23: *"ids like D306, D307,
// D307b, or D310 in messages should all be hyperlinks to the actual item"*).
const REF_RE = /\b([FDR]\d+[a-z]?)\b/g;

// The one address of an item's card — every link to one is built here, so no call site
// leaves an id unencoded beside an encoded one.
export const refHref = (id) => `#/messages?focus=${encodeURIComponent(id)}`;

//: Past this a subject is a paragraph rather than a label, and it would swamp the sentence it
//: sits in. The tooltip still carries the whole of it.
const SUBJECT_MAX = 72;

/**
 * ONE ref link — the only place that decides what a reference SAYS.
 *
 * The operator's requirement (2026-11-12): *"worst case scenario is content i need to know
 * about being referenced only by an identifier"*, and his own first cut — wherever the UI
 * renders an id, it renders a link whose text is the thing's one-line subject. So the link
 * reads `R3361 — the Decisions page is an inbox sorted by urgency` where the subject is
 * known, and the bare id where it is not (an item the index does not carry, or a prose render
 * that beat the one read). Both are links; neither is ever a dead end.
 *
 * The id STAYS in the text rather than being replaced by the subject: he names items by id
 * when he answers, and a link he cannot read the id of cannot be quoted back.
 */
export function refLink(id) {
  const a = document.createElement("a");
  a.className = "ref-link";
  a.href = refHref(id);
  a.dataset.ref = id;
  paint(a, id);
  // The subjects arrive from one shared read, kicked off by the FIRST ref the console renders
  // — a page showing no references pays nothing, and the read happens once per session. A link
  // rendered before it lands fills in here rather than staying an identifier for the session.
  if (!titlesLoaded()) { loadRefTitles(); onTitles(() => paint(a, id)); }
  return a;
}

function paint(a, id) {
  const subject = titleOf(id);
  if (!subject) {
    a.textContent = id;
    a.title = `jump to ${id} on the Messages page`;
    return;
  }
  const short = subject.length > SUBJECT_MAX
    ? `${subject.slice(0, SUBJECT_MAX).replace(/\s+\S*$/, "")}…` : subject;
  a.textContent = `${id} — ${short}`;
  a.title = `${id}: ${subject}\n(jump to it on the Messages page)`;
}

function walkAndLink(root, wanted) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (n.parentElement?.closest("a, code, pre, button, select, textarea, summary")
      ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  const hits = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    REF_RE.lastIndex = 0;
    if (REF_RE.test(n.nodeValue)) hits.push(n);
  }
  for (const n of hits) {
    const frag = document.createDocumentFragment();
    let last = 0;
    REF_RE.lastIndex = 0;
    for (const m of n.nodeValue.matchAll(REF_RE)) {
      if (!wanted(m[1])) continue;
      frag.append(n.nodeValue.slice(last, m.index));
      frag.append(refLink(m[1]));
      last = m.index + m[1].length;
    }
    if (last === 0) continue;       // nothing in this node was wanted: leave it exactly as it is
    frag.append(n.nodeValue.slice(last));
    n.replaceWith(frag);
  }
  return root;
}

export function linkifyRefs(root) {
  return walkAndLink(root, () => true);
}

/**
 * Linkify ONLY the ids the item index actually carries — the variant that is safe on prose
 * nobody wrote as a maintenance record.
 *
 * `linkifyRefs` links every `F63`/`D14`/`R7` token by pattern, which is right for the Messages
 * page and wrong for a run's narration: there a bare `D1` or `F5` is ordinary text far more
 * often than an item, and a link to a card that does not exist is worse than no link. So this
 * asks the shared subject index first and leaves anything it does not know as the text it was.
 *
 * TWO PASSES, and the first one is synchronous. The index is read once per session, so the
 * first prose on a cold page renders before it lands; linking only then would make a link
 * arrive a frame after the words, which reads as ids that link sometimes. So: link what is
 * known NOW, and when the read lands walk the same node again for the rest. A node already
 * linked is skipped by the walker's own `a` rejection, so the second pass cannot double-link.
 */
export function linkifyKnownRefs(root) {
  walkAndLink(root, (id) => Boolean(titleOf(id)));
  if (!titlesLoaded()) {
    loadRefTitles();
    onTitles(() => walkAndLink(root, (id) => Boolean(titleOf(id))));
  }
  return root;
}

// The landing half of a ref link: scroll the named card (id="ref-<id>") into view and
// flash it. Returns false when the id has no card (e.g. an item the current filter hides).
export function focusRef(id) {
  const target = document.getElementById(`ref-${id}`);
  if (!target) return false;
  target.scrollIntoView({ block: "center" });
  flash(target);
  return true;
}
