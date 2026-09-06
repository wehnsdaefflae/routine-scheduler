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
//
// The title may be a plain string, OR an object { title, id } — passing an id stamps the
// heading as <h2 id="sec-{id}">, the anchor every jump to a section aims at: the Settings
// page's side-nav and its deep links (#/settings?section=<id>), the TOC, and on the routine
// page the fix links that carry a reader from a diagnosed dependency to the panel that owns
// it. The id is a stable address, so it outlives any rewording of the title above it.
// All three surfaces — routine config, the composer, and Settings — build a section the one
// way (D64/A'), with a single description that lives inside the panel.
import { el } from "/static/util.js";

export function settingsSection(title, description, ...body) {
  const id = (title && typeof title === "object") ? title.id : null;
  const heading = (title && typeof title === "object") ? title.title : title;
  const h2 = el("h2", id ? { id: `sec-${id}` } : {}, heading);
  // Header mode — no body rows: the caller (a Settings sub-view) appends its own panel(s)
  // after this pair, so emit just the heading + one standalone description line (the
  // `p.set-desc` the Settings page's side-nav, TOC and deep-link tests expect).
  if (body.length === 0) {
    return description ? [h2, el("p", { class: "set-desc muted small" }, description)] : [h2];
  }
  // Panel mode — heading + one panel wrapping the description and the body rows (the routine
  // config page and the new-conversation composer).
  return [
    h2,
    el("div", { class: "panel" },
      description
        ? el("div", { class: "muted small", style: "margin-bottom:10px" }, description)
        : null,
      ...body.filter(Boolean)),
  ];
}
