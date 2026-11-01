// A SUBJECT's GRANTS mapping, both halves — the deny-forever tombstones it carries (F783) and
// the allow-forever grants it HOLDS (D216 rank 1) — each row with the press that clears it.
//
// Clearing either sign lands the entity on the SAME state: undecided, asked on first use. That
// is why one renderer and one card serve both — a tombstone's clear loosens, a grant's clear
// tightens, and nothing else about the act differs.
//
// A Decisions-page `deny_forever` writes a `{entity: false}` row into the answering subject's
// own routine.yaml, and the engine then refuses that entity and stops asking for it. Removing
// the row is the ONLY undo, and until this module the only editor that had one was the routine
// page — so a conversation's denial was permanent, however it had been mis-clicked.
//
// ONE row renderer, two subjects, because the two write through different mechanisms and only
// the MARKUP is shared:
//
// * the ROUTINE page edits its settings DRAFT (`fieldBlock(form, "grants")`): the press changes
//   a value the operator then accepts, and the whole mapping is PATCHed wholesale — safe there
//   because the draft is re-read when a decision lands while the page is open (F193);
// * a CONVERSATION's capabilities panel has no draft and no accept — every card writes on
//   click — so its press clears ONE named row through the conversation router
//   (`web/api_conversation_grants.py`). A wholesale replace from a page that cannot re-read
//   first would drop whatever row arrived in between, and the rows in that same mapping
//   include `secret:` allow-forever grants.
//
// Clearing only ever LOOSENS a denial: the entity goes back to undecided — asked on first use,
// requestable again — which is why neither press needs a capability decision behind it.

import { api } from "/static/api.js";
import { act, el, skeleton } from "/static/util.js";

const SECRET = "secret:";

/** The tombstones in a `grants:` mapping: the FALSE rows of any class but `secret:`, sorted.
 *  A true row is an allow-forever grant, not a denial. */
export function declinedIds(grants) {
  const g = grants || {};
  return Object.keys(g).filter((k) => !k.startsWith(SECRET) && g[k] === false).sort();
}

// The TRUE rows are selected SERVER-side (`api_conversation_grants._held`) and the card renders
// the list it answers with, so there is deliberately no `heldIds` counterpart to `declinedIds`
// here: `declinedIds` exists because the ROUTINE page filters a draft value it already holds in
// the browser. One selector per reader, none unused.

/** The rows themselves. `onRemove(eid, button)` does whatever that page's press means.
 *  `attr` names the row hook and `empty` the nothing-here line, so the two halves of the
 *  mapping render through this ONE renderer instead of a copy each. */
export function declinedRows(ids, onRemove, { attr = "data-declined-row",
                                              empty = "nothing declined",
                                              title = "make it requestable again" } = {}) {
  if (!ids.length) return el("div", { class: "muted small" }, empty);
  return el("div", {}, ...ids.map((eid) => {
    const remove = el("button", { type: "button", class: "btn small", title }, "remove");
    remove.onclick = () => onRemove(eid, remove);
    return el("div", { class: "row", style: "margin:5px 0", [attr]: eid },
      el("code", { class: "small", style: "min-width:240px" }, eid), remove);
  }));
}

/** The LIVE card for ONE half of a subject's `grants:` mapping: reads its rows from `base`'s
 *  router and clears one per press. For a surface that writes on click rather than through a
 *  draft — a conversation's capabilities panel.
 *
 *  `path` is the router's half (`declined` | `grants`), `key` the field its reader answers
 *  with, and `attr`/`cardAttr` the test hooks. One implementation, both halves: the read, the
 *  press, the refusal path and the reload are the same act whichever sign is going, and the
 *  second copy is what this campaign exists to remove. */
function grantsHalfCard(slug, { base, path, key, cardAttr, rowAttr, empty, title, said }) {
  const root = `${base}/${slug}/${path}`;
  const box = el("div", { [cardAttr]: "" }, skeleton(["50%"]));

  const load = async () => {
    let body;
    try { body = await api(root); }
    catch (err) { box.replaceChildren(el("div", { class: "muted" }, err.message)); return; }
    // `act` returns the handler's value, or undefined when it threw — so the handler RETURNS
    // the response: a refused clear must leave the row on screen, not quietly remove it.
    box.replaceChildren(declinedRows(body[key] || [], (eid, button) => act(button,
      () => api(`${root}/clear`, { method: "POST", body: { entity: eid } }),
      said(eid)).then((ok) => { if (ok) load(); }),
      { attr: rowAttr, empty, title }));
  };

  load();
  return box;
}

/** The deny-forever tombstones a subject carries, each clearable (back to undecided). */
export function declinedAccessCard(slug, { base = "/api/conversations" } = {}) {
  return grantsHalfCard(slug, { base, path: "declined", key: "declined",
    cardAttr: "data-declined-card", rowAttr: "data-declined-row",
    empty: "nothing declined", title: "make it requestable again",
    said: (eid) => `${eid} is requestable again` });
}

/** The allow-forever grants a subject HOLDS, each withdrawable (back to undecided — asked on
 *  first use, not denied). The denial half has had this card since F783; the allow half had no
 *  surface at all on the conversation side, so a grant a Decisions click wrote could neither be
 *  read nor taken back (D216 rank 1). */
export function heldGrantsCard(slug, { base = "/api/conversations" } = {}) {
  return grantsHalfCard(slug, { base, path: "grants", key: "held",
    cardAttr: "data-held-card", rowAttr: "data-held-row",
    empty: "nothing granted forever", title: "withdraw it (asked on first use again)",
    said: (eid) => `${eid} is undecided again` });
}
