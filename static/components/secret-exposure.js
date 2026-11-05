// A SUBJECT's exposure to the SHARED secrets store — one three-state dial per store name:
// "ask on first use" (no `grants:` row), "expose" (`secret:<NAME>: true`), "withhold"
// (`secret:<NAME>: false`).
//
// ONE row renderer, two subjects, because only the MARKUP is shared and the WRITES differ in
// kind — the same split declined-access.js made for the other half of the same mapping:
//
// * the ROUTINE page edits its settings DRAFT (`fieldBlock(form, "grants")` in
//   views/routine-config-access.js): the dial changes a value the operator then accepts, and
//   the whole mapping is PATCHed wholesale — safe there because the draft is re-read when a
//   decision lands while the page is open (F193);
// * a CONVERSATION's capabilities panel has no draft and no accept — every card writes on
//   click — so its dial writes ONE named row through the conversation router
//   (`web/api_conversation_grants.py`: PUT …/secret-exposure). A wholesale replace from a page
//   that cannot re-read first would drop whatever row arrived in between, and the rows in that
//   same mapping include the deny-forever tombstones the engine honours.
//
// Before D216 part (a) the conversation side had no dial at all: `api_conversation_grants.py`
// could only CLEAR a row that already existed, so the exposure decision for a thread was
// reachable only by the engine asking at first use, and withholding a name in advance meant
// hand-editing routine.yaml. The routine page has had the dial since D103.
//
// The store's NAMES come from `/api/settings/secrets` on both pages — that endpoint is denied
// to the routine token (`app.ROUTINE_TOKEN_DENIED_READS`), so no subject-scoped router mirrors
// them.

import { api } from "/static/api.js";
import { act, el, skeleton } from "/static/util.js";

const SECRET = "secret:";

/** The store's names (sorted), or an `error` the caller renders in place of the rows. */
export function secretStore() {
  return api("/api/settings/secrets").catch((err) => ({ error: err.message, keys: [] }));
}

/** The `secret:` rows of a `grants:` mapping, keyed by STORE NAME — what the dial is per. */
export function exposureRows(grants) {
  return Object.fromEntries(Object.entries(grants || {})
    .filter(([k]) => k.startsWith(SECRET)).map(([k, v]) => [k.slice(SECRET.length), v]));
}

/** The dials themselves: one row per name, each a three-state select.
 *
 *  `rows` is {NAME: bool} (an absent name = undecided) and `onSet(name, value, select)` does
 *  whatever that page's change MEANS — move a draft, or write one row. `value` is `true`,
 *  `false` or `null` for undecided, which is the ABSENCE of a row rather than a third value.
 *  `keys` names the store, so a row the store no longer carries is still shown (and flagged):
 *  a stale row is a decision the engine still honours, and a row the operator can see is a row
 *  the operator can change. */
export function exposureDials(names, rows, onSet, { keys = [] } = {}) {
  if (!names.length) return el("div", { class: "muted small" }, "no secrets in the store yet");
  return el("div", {}, ...names.map((name) => {
    const sel = el("select", { "data-nopersist": true });
    sel.append(el("option", { value: "" }, "ask on first use"),
      el("option", { value: "true" }, "expose"),
      el("option", { value: "false" }, "withhold"));
    sel.value = name in rows ? String(!!rows[name]) : "";
    sel.onchange = () => onSet(name, sel.value === "" ? null : sel.value === "true", sel);
    return el("div", { class: "row", style: "margin:5px 0", "data-secret-row": name },
      el("code", { class: "small", style: "min-width:240px" }, name), sel,
      keys.includes(name) ? null
        : el("span", { class: "muted small" }, "not in the store (stale entry)"));
  }));
}

/** The LIVE card for a subject that writes on CLICK rather than through a draft — a
 *  conversation's capabilities panel. Reads its rows from `base`'s router and writes ONE named
 *  row per change; a refused write leaves the dial where the server says it is, because the
 *  reload comes from the response rather than from the press. */
export function secretExposureCard(slug, { base = "/api/conversations" } = {}) {
  const root = `${base}/${slug}/secret-exposure`;
  const box = el("div", { "data-exposure-card": "" }, skeleton(["50%"]));

  const load = async () => {
    let body;
    let store;
    try { [body, store] = await Promise.all([api(root), secretStore()]); }
    catch (err) { box.replaceChildren(el("div", { class: "muted" }, err.message)); return; }
    const rows = body.exposure || {};
    const names = [...new Set([...(store.keys || []), ...Object.keys(rows)])].sort();
    box.replaceChildren(...[
      store.error ? el("div", { class: "muted small" }, store.error) : null,
      exposureDials(names, rows, (name, value, sel) => act(sel,
        () => api(root, { method: "PUT", body: { name, expose: value } }),
        value === null ? `${name}: asked on first use`
          : `${name}: ${value ? "exposed" : "withheld"}`).then((ok) => { if (ok) load(); }),
        { keys: store.keys || [] }),
    ].filter(Boolean));
  };

  load();
  return box;
}
