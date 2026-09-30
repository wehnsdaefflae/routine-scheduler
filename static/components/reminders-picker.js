// SHARED REMINDERS — which of the library's curated cautions this routine takes on.
//
// A routine's own reminders need no list: a run leaves them for itself and they hold its own
// actions. A SHARED one is a caution another routine learned and the library curated — a
// (pattern → consequence) pair that HOLDS a matching action before it runs, so the run decides
// again with the caution in front of it. Taking one on is a choice, which is why it is a list
// here: the pattern proposes the ones that fit its kind of work; the person ticks.
//
// Each row shows what the reminder DOES — the action pattern it matches and the consequence it
// names — because an id says nothing a person can decide on.

import { el } from "/static/util.js";

/**
 * remindersPicker({ available, value, set }) → node
 * `available` is the library's shared reminders (GET /api/library → `reminders`: {id, regex,
 * description}); `value` the draft list of ids; `set(ids)` reports an edit.
 */
export function remindersPicker({ available = [], value = [], set }) {
  const chosen = new Set(value || []);
  const known = new Set(available.map((r) => r.id));
  const report = () => set([...chosen]);
  const rowFor = (r, missing = false) => {
    const box = el("input", { type: "checkbox", checked: chosen.has(r.id) ? "" : null,
      "data-nopersist": true,
      onchange: () => { if (box.checked) chosen.add(r.id); else chosen.delete(r.id); report(); } });
    return el("label", { class: `rem-row${missing ? " missing" : ""}`, "data-reminder": r.id },
      box,
      el("div", { class: "rem-main" },
        el("div", { class: "rem-head" }, el("code", { class: "rem-id" }, r.id)),
        missing
          ? el("div", { class: "small tr-warn" }, "no longer in the library — untick to clear it")
          : el("div", { class: "rem-body" },
              el("div", { class: "rem-when small" }, "holds an action matching ",
                el("code", {}, r.regex)),
              el("div", { class: "rem-why prose small" }, r.description || ""))));
  };
  const stale = [...chosen].filter((id) => !known.has(id)).map((id) => ({ id }));
  return el("div", { class: "rem-picker", "data-reminders-picker": "" },
    available.length || stale.length
      ? el("div", { class: "rem-list" },
          ...available.map((r) => rowFor(r)), ...stale.map((r) => rowFor(r, true)))
      : el("div", { class: "muted small" },
          "the library curates no shared reminders yet — a run proposes one with its approval; "
          + "they are removed on the Library page"));
}
