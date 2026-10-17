// The OUTCOME panel of a routine's development view — what the routine exists to produce, against
// what it cost to produce it (GET /api/changes/<slug>/outcome, readmodels/outcome_series.py).
//
// It leads the view on purpose: every other reading there is about the routine's PROCESS (did its
// runs fail, meet their lines, cost more), and a routine can be healthy on all of them while it
// produces nothing — freelance-radar was, through 89 applications and no interview. One row per
// declared measure: how it moved over the window (a count's change, a level's first → last) and
// the context volume each unit of progress cost; beneath, the window's runs, volume and notional
// dollars. Nothing the routine says about itself is shown here. An undeclared contract says so,
// in the same words the setup surface's blocking row uses.

import { api } from "/static/api.js";
import { el, fmtNum, fmtUsd } from "/static/util.js";

const th = (label, title = "") => el("th", { title: title || null }, label);

function moved(m) {
  if (m.readings < 2) return el("span", { class: "faint" }, m.readings ? "one reading" : "no reading");
  const better = m.better === "higher" ? m.change > 0 : m.change < 0;
  const cls = m.change === 0 ? "faint" : better ? "ok" : "bad";
  const text = m.kind === "level" ? `${fmtNum(m.first)} → ${fmtNum(m.last)}`
    : `${m.change > 0 ? "+" : ""}${fmtNum(m.change)}`;
  return el("span", { class: `outcome-move ${cls}` }, text);
}

function table(data) {
  return el("div", { class: "tablewrap" }, el("table", { class: "list stack outcome-table" },
    el("thead", {}, el("tr", {}, th("measure"), th("moved", `over the last ${data.weeks} weeks`),
      th("volume per unit", "the window's context volume for each unit of progress — none when it made none"))),
    el("tbody", {}, ...data.measures.map((m) => el("tr", { "data-measure": m.id },
      el("td", {}, m.label, el("span", { class: "mono faint small" }, ` ${m.id}`)),
      el("td", { class: "num inline", "data-label": "moved" }, moved(m)),
      el("td", { class: "num inline", "data-label": "per unit" },
        m.kind !== "count" ? "—" : m.per_unit == null ? el("span", { class: "bad" }, "no progress")
          : fmtNum(m.per_unit)))))));
}

function costLine(data) {
  const c = data.cost;
  const parts = [`${c.runs} runs`, `${fmtNum(c.volume)} volume`];
  if (c.notional_runs) parts.push(`${fmtUsd(c.notional)} notional (${c.notional_runs} priced)`);
  if (c.unmeasured_runs) parts.push(`${c.unmeasured_runs} unmeasured`);
  if (data.unreadable_runs) parts.push(`${data.unreadable_runs} without an outcome reading`);
  return el("div", { class: "sub" }, `last ${data.weeks} weeks: ${parts.join(" · ")}`);
}

/** Fill `box` with the routine's outcome; returns `{ reload }`. */
export function mountOutcome(box, slug) {
  let seq = 0;
  async function reload() {
    const mine = ++seq;
    let data;
    try { data = await api(`/api/changes/${encodeURIComponent(slug)}/outcome`); }
    catch (err) {
      if (mine === seq) box.replaceChildren(el("div", { class: "faint small" }, `couldn't load: ${err.message}`));
      return;
    }
    if (mine !== seq) return;
    if (!data.declared) {
      box.replaceChildren(el("div", { class: "dev-none summons small", "data-outcome": "undeclared" },
        "it declares no outcome — nothing says, as a number, what it exists to produce for its principal"));
      return;
    }
    box.replaceChildren(el("div", { class: "sub" }, `for ${data.principal}`), table(data), costLine(data));
  }
  reload().catch(() => {});
  return { reload };
}
