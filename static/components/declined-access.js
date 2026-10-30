// DECLINED ACCESS (F783) — the deny-forever tombstones a subject carries, and the press that
// clears one.
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

/** The rows themselves. `onRemove(eid, button)` does whatever that page's press means. */
export function declinedRows(ids, onRemove) {
  if (!ids.length) return el("div", { class: "muted small" }, "nothing declined");
  return el("div", {}, ...ids.map((eid) => {
    const remove = el("button", { type: "button", class: "btn small",
                                  title: "make it requestable again" }, "remove");
    remove.onclick = () => onRemove(eid, remove);
    return el("div", { class: "row", style: "margin:5px 0", "data-declined-row": eid },
      el("code", { class: "small", style: "min-width:240px" }, eid), remove);
  }));
}

/** The LIVE card: reads the subject's declined rows from `base`'s router and clears one per
 *  press. For a surface that writes on click rather than through a draft — a conversation's
 *  capabilities panel. */
export function declinedAccessCard(slug, { base = "/api/conversations" } = {}) {
  const root = `${base}/${slug}/declined`;
  const box = el("div", { "data-declined-card": "" }, skeleton(["50%"]));

  const load = async () => {
    let body;
    try { body = await api(root); }
    catch (err) { box.replaceChildren(el("div", { class: "muted" }, err.message)); return; }
    // `act` returns the handler's value, or undefined when it threw — so the handler RETURNS
    // the response: a refused clear must leave the row on screen, not quietly remove it.
    box.replaceChildren(declinedRows(body.declined || [], (eid, button) => act(button,
      () => api(`${root}/clear`, { method: "POST", body: { entity: eid } }),
      `${eid} is requestable again`).then((ok) => { if (ok) load(); })));
  };

  load();
  return box;
}
