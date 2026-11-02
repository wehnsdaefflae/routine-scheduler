// What ONE run of this routine costs (GET /api/changes/<slug>/cost, readmodels/cost_profile.py,
// D163) — the figure a budget is actually set in. It sits beside the Outcome panel in a routine's
// development view, because the two answer one question between them: what the routine produces,
// and what a run of it costs to produce it. The other cost readings on this instance answer
// neither: the Stats tab rolls spend up per calendar MONTH, and Recipe health reduces the trend
// to a flag with a median buried in a `reasons` string.
//
// HONESTY RULE, and it is the whole reason this panel is not three numbers: the reader decides
// whether its own figures can be TRUSTED. A depth-0 run reporting less volume than its own
// `#sub<n>` child rows add up to was understated by the accounting defect closed in 0.494.0
// (R3112), so a window straddling the fix compares two different quantities and a median over it
// reads as a cost improvement nobody made. `mixed_accounting` names the contaminated side and
// `integrity.detail` says it in prose — so that prose is rendered VERBATIM here, the affected
// window is marked where its median is read, and a flagged trend across that boundary says what
// it is measuring. Nothing here re-derives a verdict the reader already reached.

import { api } from "/static/api.js";
import { el, fmtNum, fmtUsd } from "/static/util.js";

const num = (v) => (v == null ? "—" : fmtNum(v));
const MIXED_WHY = "this window holds runs the 0.494.0 accounting defect understated — its "
  + "median is a floor, not a figure";

/** The headline: what a run of it typically costs, over EVERY run — never one flagged window. */
function typical(p) {
  const t = p.typical;
  const parts = [`${num(t.volume_median)} volume`, `${num(t.turns_median)} turns`];
  if (t.notional_priced) parts.push(`${fmtUsd(t.notional_median)} notional`);
  return [
    el("div", { class: "cost-figure", "data-cost-typical": "" }, parts.join(" · "),
      el("span", { class: "faint" }, " per run")),
    el("div", { class: "sub" },
      `median over ${p.runs} run${p.runs === 1 ? "" : "s"}`
      + (t.measured < t.runs ? ` · ${t.runs - t.measured} unmeasured` : "")
      + (t.notional_priced ? ` · ${t.notional_priced} priced` : " · none priced")
      + ` · dearest ${num(t.volume_max)} volume`),
  ];
}

function windowRow(label, side) {
  return el("tr", { "data-cost-window": label },
    el("td", {}, label,
      side.runs && !side.trustworthy
        ? el("span", { class: "cost-mixed", title: MIXED_WHY }, " · mixed")
        : null),
    el("td", { class: "num inline", "data-label": "runs" }, String(side.runs || 0)),
    el("td", { class: "num inline", "data-label": "volume" },
      side.runs ? num(side.volume_median) : "—"),
    el("td", { class: "num inline", "data-label": "turns" },
      side.runs ? num(side.turns_median) : "—"));
}

/** The pair `recent_trend` judged — the same two windows, so the panel and the flag agree. */
function windows(p) {
  const th = (l, title = "", cls = "") => el("th", { title: title || null, class: cls || null }, l);
  return el("div", { class: "tablewrap" }, el("table", { class: "list stack cost-table" },
    el("thead", {}, el("tr", {}, th("window", `the last ${p.trend.window} runs against the `
      + `${p.trend.window} before them — the pair the trend flag judges`),
      th("runs", "", "num"), th("volume", "median per run", "num"),
      th("turns", "median per run", "num"))),
    el("tbody", {}, windowRow("baseline", p.baseline), windowRow("recent", p.recent))));
}

/** The trend verdict, passed through from the flag — never re-derived here. */
function trendLine(p) {
  const t = p.trend;
  if (!t.evaluated) {
    return el("div", { class: "faint small" },
      "too few runs on one side to compare windows — the figure above still stands");
  }
  if (!t.flagged) return el("div", { class: "faint small" }, "no cost regression flagged");
  const mixed = p.mixed_accounting.length
    ? " — and the comparison straddles the 0.494.0 accounting fix, so part of this fall or rise "
      + "is the fix, not the routine"
    : "";
  return el("div", { class: "cost-flagged small" },
    `⚠ flagged: ${(t.reasons || []).join(" · ")}${mixed}`);
}

/** The integrity verdict IN WORDS, as the reader itself wrote it. */
function integrityNote(p) {
  const i = p.integrity;
  if (!i.understated_runs) return el("div", { class: "faint small" }, i.detail);
  const sides = p.mixed_accounting;
  return el("div", { class: "panel warn cost-integrity" },
    el("div", {}, "⚠ mixed accounting — the figures above span two different quantities"),
    el("div", { class: "small", style: "margin-top:4px" }, i.detail),
    el("div", { class: "small", style: "margin-top:4px" }, sides.length
      ? `The ${sides.join(" and ")} window above carries understated runs: read its median as a `
        + "floor. A fall across that boundary measures the accounting fix of 0.494.0."
      : "Both compared windows are sound; the understated runs are older ones outside them."));
}

/** Fill `box` with the routine's per-run cost; returns `{ reload }` like mountOutcome. */
export function mountCost(box, slug) {
  let seq = 0;
  async function reload() {
    const mine = ++seq;
    let p;
    try { p = await api(`/api/changes/${encodeURIComponent(slug)}/cost`); }
    catch (err) {
      if (mine === seq) {
        box.replaceChildren(el("div", { class: "faint small" }, `couldn't load: ${err.message}`));
      }
      return;
    }
    if (mine !== seq) return;
    if (!p.runs) {
      box.replaceChildren(el("div", { class: "dev-none faint small", "data-cost": "no-runs" },
        "no run in the usage stream yet — a per-run cost appears once it has run"));
      return;
    }
    box.replaceChildren(integrityNote(p), ...typical(p), windows(p), trendLine(p));
  }
  reload().catch(() => {});
  return { reload };
}
