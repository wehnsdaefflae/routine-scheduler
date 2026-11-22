// How the Routines list is ORDERED and FILTERED — the vocabulary behind the table's sortable
// headers, the state chips and the search box.
//
// Its own module because the page it serves is at the house size bar: a sort key, a state bucket
// and the two pure functions over them are one responsibility (which routines are shown, in what
// order), and the view's job is the page — its panels, its loads, its live refresh. The view
// keeps the sort STATE (which key, which direction, what is typed) because that is the user's
// current choice and is persisted per browser; this module only says what the choices MEAN.

import { WORKING as RUNNING } from "/static/states.js";

const tokensOf = (c) => (c.last_run?.usage?.in || 0) + (c.last_run?.usage?.out || 0);

// sort keys: [label, value-fn, descending?]
export const SORTS = {
  activity: ["recent activity", (c) => c.last_run?.ts || "", true],
  name: ["name", (c) => (c.name || c.slug).toLowerCase(), false],
  next: ["next run", (c) => c.next_fire || "9999", false],
  state: ["state", (c) => c.active_state || (c.last_run?.state ?? "zz"), false],
  cost: ["last cost", (c) => c.last_run?.usage?.cost || 0, true],
  tokens: ["last tokens", tokensOf, true],
  turns: ["last turns", (c) => c.last_run?.turns || 0, true],
  duration: ["last duration", (c) => c.last_run?.elapsed_s || 0, true],
  questions: ["open questions", (c) => c.open_questions || 0, true],
};

// coarse run-state buckets for the state filter chips
export const STATE_BUCKETS = {
  active: (c) => RUNNING.has(c.active_state),
  waiting: (c) => c.active_state === "waiting_user" || (c.open_questions || 0) > 0,
  ok: (c) => !c.active_state && c.last_run?.state === "finished",
  failed: (c) => !c.active_state && ["failed", "aborted"].includes(c.last_run?.state),
  // FINISHED before DISABLED, never both: a routine that reached its finish line is a different
  // thing from one you switched off — lumping them lost the only state that says "this job is
  // over". `retired` is derived from the finish line; `enabled` is your switch.
  finished: (c) => c.retired,
  disabled: (c) => !c.enabled && !c.retired,
};

/** Does this card pass the state chips and the search box? (`search` is already lowercased.) */
export function visible(c, { states, search }) {
  if (states.size && ![...states].some((s) => STATE_BUCKETS[s]?.(c))) return false;
  if (search) {
    const hay = `${c.name} ${c.slug} ${c.description} ${(c.tags || []).join(" ")}`.toLowerCase();
    if (!hay.includes(search)) return false;
  }
  return true;
}

/** The cards in the chosen order. `sortDir` (F208) overrides the column's natural direction. */
export function ordered(list, { sortKey, sortDir }) {
  const [, valueOf, desc] = SORTS[sortKey] || SORTS.activity;
  const descending = sortDir ? sortDir === "desc" : desc;
  return [...list].sort((a, b) => {
    const va = valueOf(a), vb = valueOf(b);
    const cmp = typeof va === "string" ? va.localeCompare(vb) : va - vb;
    return descending ? -cmp : cmp;
  });
}
