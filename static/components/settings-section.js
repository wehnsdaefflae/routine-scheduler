// The shared "settings section" primitive — a titled block (heading + panel + an optional
// one-line description) used by BOTH the routine config page and the new-conversation
// composer, so a setting reads and looks the same wherever it appears (D57). Emitting an
// <h2> keeps the routine page's section grouping (routine-overview.groupSections) and the
// side table-of-contents working; the description is the per-control copy the operator
// reads before touching the section — one job per element (a label labels, this explains).
//
// Returns the [<h2>, <div.panel>] pair so a caller spreads it into a parent:
//   view.append(...settingsSection("Budgets", "hard per-run ceilings…", ...rows));
// Passing an empty/undefined description omits the description line entirely.
import { el } from "/static/util.js";

export function settingsSection(title, description, ...body) {
  return [
    el("h2", {}, title),
    el("div", { class: "panel" },
      description
        ? el("div", { class: "muted small", style: "margin-bottom:10px" }, description)
        : null,
      ...body.filter(Boolean)),
  ];
}
