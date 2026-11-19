// Wrong turns, grouped by CAUSE (2026-11-19 · `readmodels/wrong_turns.py`).
//
// A wrong turn came back well-formed and WRONG — schema-rejected, repeated, out of attempts —
// and the engine re-drove it. It used to be counted on the Blocked work panel, where ~230 a week
// buried the rows that panel exists for: a rejected turn's run goes on, so nothing due failed to
// happen. What the measurement is FOR is this table — which mistake recurs, across which routines
// and models — because each row is one fix in the engine, the schema or the prompt. The server
// masks what varies between occurrences (turn number, numbers, quoted values), so the same error
// from two routines is one row.

import { api } from "/static/api.js";
import { el, emptyState, fmtInt } from "/static/util.js";
import { foldRows, routineHref, statSection } from "/static/views/stats-parts.js";

const WINDOW_DAYS = 7;
const when = (ts) => (ts || "").replace("T", " ").slice(0, 16) || "—";

// `{name: count}` as "name ×n, name ×n", the head only — the full list is the cell's title.
function tally(counts, link) {
  const pairs = Object.entries(counts || {});
  const head = pairs.slice(0, 3).map(([name, n]) => {
    const label = `${name}${n > 1 ? ` ×${n}` : ""}`;
    return link && name ? el("a", { href: routineHref(name, "routine") }, label) : el("span", {}, label);
  });
  const parts = head.flatMap((node, i) => (i ? [", ", node] : [node]));
  if (pairs.length > 3) parts.push(el("span", { class: "faint" }, ` +${pairs.length - 3}`));
  return el("td", { class: "small", title: pairs.map(([k, n]) => `${k} ×${n}`).join("\n") },
    ...parts);
}

/** The wrong-turns section, or null when the endpoint cannot be read. */
export async function wrongTurnsSection() {
  let d;
  try {
    d = await api(`/api/health/wrong-turns?days=${WINDOW_DAYS}`);
  } catch {
    return null;
  }
  const rows = d.rows || [];
  const sub = `turns that came back well-formed and wrong in the last ${d.window_days} days, `
    + "grouped by cause. Not blocked work — each was re-driven and its run went on — but every "
    + "row is a mistake that recurs, and one fix in the engine, schema or prompt removes it.";
  if (!rows.length) {
    return el("div", { class: "stat-section" }, el("h2", {}, "Wrong turns"),
      el("div", { class: "sub" }, sub),
      emptyState("✓", "no wrong turns", "every turn in the window was taken as asked"));
  }
  const head = el("tr", {},
    el("th", { class: "num" }, "turns"), el("th", {}, "cause"), el("th", {}, "routines"),
    el("th", {}, "models"), el("th", {}, "newest"));
  const body = rows.map((r) => el("tr", {},
    el("td", { class: "num" }, fmtInt(r.count)),
    el("td", { class: "small", title: r.detail || "" },
      el("div", {}, r.cause), el("div", { class: "faint small" }, r.reason || "")),
    tally(r.routines, true),
    tally(r.models, false),
    el("td", { class: "faint small" }, when(r.last_ts))));
  const more = d.causes > rows.length ? ` (the ${fmtInt(rows.length)} most frequent shown)` : "";
  return statSection("Wrong turns",
    [sub, el("div", {}, `${fmtInt(d.total)} wrong turns from ${fmtInt(d.causes)} causes${more}`)],
    head, foldRows(body, 8, "causes"));
}
