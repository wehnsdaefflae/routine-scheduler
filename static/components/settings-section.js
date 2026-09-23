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
  return [h2, el("div", { class: "panel" }, sectionAbout(description), ...body.filter(Boolean))];
}

//: Where a description stops being a line and becomes a paragraph. Above it the reader gets the
//: first sentence and opens the rest; below it folding costs more attention than it saves.
const LEAD_MAX = 160;

/** The section's explanation — one job, one shape, at both measures.
 *
 * It used to be an uncapped `div` in panel mode and a 68ch-capped `p.set-desc` in header mode,
 * so the SAME voice ran to 1 350px in one place and 450px in the other on one screen. One class
 * now, capped by one rule.
 *
 * On a PHONE it is also not in the reading path. A routine page opened with 13 lines on
 * SCHEDULE, 22 on DOMAIN, 20 on PERMISSIONS before their first control — correct, wanted, and
 * read again every time a dial is changed; at 390px that is four screens of prose per group,
 * where at 1440px the same copy is a paragraph beside the controls it explains. Below 861px the
 * first sentence leads and the rest is one tap away. Nothing is shortened at either width.
 */
function sectionAbout(description) {
  if (!description) return null;
  // a description is a string, or the parts of one — most of the routine page's carry an inline
  // <span> (a slug chip, a link) between two strings, so the parts arrive as an array
  const parts = (Array.isArray(description) ? description : [description]).filter(Boolean);
  const chars = parts.reduce((n, p) => n + (typeof p === "string" ? p.length : 0), 0);
  const wide = window.matchMedia("(min-width: 861px)").matches;
  if (wide || chars <= LEAD_MAX || typeof parts[0] !== "string")
    return el("p", { class: "set-desc muted small" }, ...parts);
  const head = parts[0];
  const stop = head.search(/[.?!](\s|$)/);
  const space = head.lastIndexOf(" ", LEAD_MAX);
  const cut = stop > 0 && stop < LEAD_MAX ? stop + 1 : (space > 0 ? space : LEAD_MAX);
  return el("details", { class: "set-about" },
    el("summary", { class: "set-desc muted small" },
      head.slice(0, cut).trim(), el("span", { class: "faint" }, " more…")),
    el("p", { class: "set-desc muted small" }, head.slice(cut).trim(), ...parts.slice(1)));
}
