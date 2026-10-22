// What the fleet OWED and did not deliver — the health stream's blocked-work events, rendered
// (R2653).
//
// `readmodels/health_stream.py` + `GET /api/health/blocked` shipped in 0.3xx and no console ever
// read them: measured at 0.432.0, `health/blocked` had no hit anywhere in static/. The endpoint
// says so about itself — its handler docstring explains that it ships a `vocabulary` payload "so
// the console renders a label it was not compiled with", a piece of UI design for a consumer
// that did not exist. So the events it folds were, from the operator's chair, as invisible as
// before the read model: a refused fire leaves no run, a stopped chain leaves no member run, a
// capped trigger leaves a dark routine. `backup_stale` is the proof — added to that vocabulary
// specifically so this surface would render it without recompiling, after nineteen unnoticed
// nights.
//
// It sits on Stats because Stats is already "how is the fleet doing", and `api_health.py` draws
// the distinction this panel completes: Stats answers what the fleet SPENT, this answers what it
// OWED and did not deliver. Whether health eventually earns a tab of its own is the operator's
// call and does not block the reading.
//
// ZERO IS THE GOOD READING, and that is exactly why the panel renders when there is nothing:
// an absent panel and a healthy one are indistinguishable, and only one of them reassures.
//
// Three shapes of `subject`, and the fold's contract on them: a routine slug, a LANE ID (opaque
// by contract — never parsed, never prefix-read), or empty for an instance-wide event like a
// scheduler tick or a stale backup. Each gets the link it can have and no link it cannot.

import { api } from "/static/api.js";
import { el, emptyState, fmtInt } from "/static/util.js";
import { foldRows, statSection } from "/static/views/stats-parts.js";

const WINDOW_DAYS = 7;          // the fold's own default: a week covers every routine's cadence

// A stamp as a reader wants it in a table cell: the day and the minute, nothing else.
const when = (ts) => (ts || "").replace("T", " ").slice(0, 16) || "—";

// A routine slug links to its page. A lane id is opaque, so it links to the Routines page where
// the lanes live rather than to a route built out of its characters. Empty means the instance
// itself and names that instead of rendering a blank cell.
function subjectCell(subject) {
  if (!subject) return el("td", { class: "faint" }, "the instance");
  if (subject.startsWith("grp-")) return el("td", {},
    el("a", { href: "#/routines", title: `lane ${subject}` }, "a lane"),
    el("span", { class: "faint small" }, ` ${subject}`));
  return el("td", {}, el("a", { href: `#/routine/${subject}` }, subject));
}

/**
 * The blocked-work section, or null if the endpoint cannot be read (the panel is a reading of
 * the fleet, never a reason for the page to fail). Awaits its own fetch: the caller appends it.
 */
export async function blockedSection() {
  let d;
  try {
    d = await api(`/api/health/blocked?days=${WINDOW_DAYS}`);
  } catch {
    return null;
  }
  const rows = d.rows || [];
  const sub = [`what the fleet owed and did not do in the last ${d.window_days || WINDOW_DAYS} `
    + "days — refused fires, stopped lane chains, capped triggers, commits that did not land, "
    + "a stale backup. None of these produces a run, so no other surface on this page can "
    + "carry them.",
  ];

  if (!rows.length) {
    return el("div", { class: "stat-section" },
      el("h2", {}, "Blocked work"),
      el("div", { class: "sub" }, ...sub),
      emptyState("✓", "nothing blocked",
        `no work was owed and skipped in the last ${d.window_days || WINDOW_DAYS} days`));
  }

  const head = el("tr", {},
    el("th", {}, "what did not happen"), el("th", {}, "whose"),
    el("th", { class: "num" }, "times"), el("th", {}, "first"), el("th", {}, "newest"),
    el("th", {}, "newest detail"));
  const body = rows.map((r) => el("tr", {},
    // the label is the SERVER's (`means`), which is what the vocabulary payload is for.
    // `corrected` (F637/F703): the run behind this row was resumed and finished ok. The row
    // STAYS — a stopped chain's remaining members really did not run — but a reader must not
    // keep reading it as a failure, and the server's own detail says what was superseded.
    el("td", {}, el("div", {}, r.event),
      r.corrected ? el("div", { class: "faint small" }, "corrected — the run behind this was "
        + "resumed and finished ok") : null,
      el("div", { class: "faint small" }, r.means || "")),
    subjectCell(r.subject),
    el("td", { class: "num" }, fmtInt(r.count)),
    el("td", { class: "faint small" }, when(r.first_ts)),
    el("td", { class: "faint small" }, when(r.last_ts)),
    el("td", { class: "small" }, r.detail || "")));

  return statSection("Blocked work",
    [...sub, el("div", {}, `${fmtInt(d.total)} in window · zero is the good reading`)],
    head, foldRows(body, 8, "rows"));
}
