// Stats: usage analytics across every run in the routines + conversations homes — time,
// tokens, and cost rolled up by routine, model, endpoint, day, kind, and run-state
// (served by /api/stats → rsched.stats.aggregate). Read-only; the filesystem is the truth,
// so a refresh always reflects the live state. The charts section is USER-CONFIGURABLE:
// each card is a metric × grouping × range × form over the per-run records, persisted in
// localStorage — SVG, dependency-free (components/charts.js).

import { api } from "/static/api.js";
import { GROUPS, METRICS, RANGES, chartNode, colorMap, hideTip } from "/static/components/charts.js";
import { el, emptyState, fmtDur, fmtInt, fmtNum, fmtUsd, skeleton, storage } from "/static/util.js";

const CHARTS_KEY = "rsched.stats.charts";
const DEFAULT_CHARTS = [
  { metric: "tokens", group: "none", range: 30, type: "bars" },
  { metric: "cost", group: "endpoint", range: 30, type: "bars" },
];

function loadChartSpecs() {
  try {
    const got = JSON.parse(storage.get(CHARTS_KEY) || "");
    if (Array.isArray(got) && got.length) return got;
  } catch { /* fall through to defaults */ }
  return DEFAULT_CHARTS.map((c) => ({ ...c }));
}

const NBSP = " ";

function tokensOf(d) {
  return (d.tokens_in || 0) + (d.tokens_out || 0);
}

function card(label, value, sub) {
  return el("div", { class: "stat-card" },
    el("div", { class: "stat-value" }, value),
    el("div", { class: "stat-label" }, label),
    sub ? el("div", { class: "stat-sub" }, sub) : null);
}

// The configurable charts panel: each card = one chart spec the user edits inline;
// specs persist in localStorage, colors stay entity-stable per grouping dimension.
function chartsSection(runs) {
  const specs = loadChartSpecs();
  const colorFor = {};   // per grouping dimension, computed once over the WHOLE dataset
  const colorOf = (group) => (colorFor[group] ??= colorMap(runs, group));
  const box = el("div", { class: "stat-section" });
  const save = () => storage.set(CHARTS_KEY, JSON.stringify(specs));

  const sel = (options, value, onchange, labelOf = (v) => v) => {
    const node = el("select", { class: "chart-sel", "data-nopersist": "" },
      ...options.map((o) => el("option", { value: o, ...(String(o) === String(value) ? { selected: true } : {}) },
        labelOf(o))));
    node.onchange = () => onchange(node.value);
    return node;
  };

  function chartCard(spec) {
    const card = el("div", { class: "panel chart-card" });
    const plot = el("div", { class: "chart-plot" });
    const redraw = () => {
      hideTip();
      plot.replaceChildren(chartNode(spec, runs, colorOf(spec.group)));
      save();
    };
    const remove = el("button", { class: "btn small", title: "remove this chart" }, "×");
    remove.onclick = () => {
      specs.splice(specs.indexOf(spec), 1);
      save();
      card.remove();
    };
    card.append(
      el("div", { class: "row chart-config" },
        sel(Object.keys(METRICS), spec.metric, (v) => { spec.metric = v; redraw(); },
            (m) => METRICS[m].label),
        sel(Object.keys(GROUPS), spec.group, (v) => { spec.group = v; redraw(); },
            (g) => GROUPS[g]),
        sel(RANGES, spec.range, (v) => { spec.range = Number(v); redraw(); },
            (r) => `${r} days`),
        sel(["bars", "line"], spec.type, (v) => { spec.type = v; redraw(); }),
        el("span", { style: "margin-left:auto" }, remove)),
      plot);
    redraw();
    return card;
  }

  const add = el("button", { class: "btn small" }, "+ add chart");
  const cards = el("div", {}, ...specs.map(chartCard));
  add.onclick = () => {
    const spec = { metric: "runs", group: "routine", range: 30, type: "bars" };
    specs.push(spec);
    save();
    cards.append(chartCard(spec));
  };
  box.append(el("h2", {}, "charts"),
    el("div", { class: "muted small", style: "margin-bottom:8px" },
      "each card is yours to shape: metric × grouping × range × form, over every kept run. ",
      "Layouts persist in this browser."),
    cards, el("div", { class: "row mt" }, add));
  return box;
}

// A table over a {key: metrics} slice, sorted by total tokens desc.
function sliceTable(title, slice, keyLabel, extraCols) {
  const rows = Object.entries(slice || {});
  if (!rows.length) return null;
  const head = el("tr", {},
    el("th", {}, keyLabel),
    ...(extraCols || []).map((c) => el("th", {}, c.label)),
    el("th", { class: "num" }, "runs"),
    el("th", { class: "num" }, "tokens in"),
    el("th", { class: "num" }, "tokens out"),
    el("th", { class: "num" }, "cost"),
    el("th", { class: "num" }, "time"));
  const body = rows.map(([k, d]) => el("tr", {},
    el("td", {}, k),
    ...(extraCols || []).map((c) => el("td", {}, c.get(d) || NBSP)),
    el("td", { class: "num" }, fmtInt(d.runs)),
    el("td", { class: "num" }, fmtInt(d.tokens_in)),
    el("td", { class: "num" }, fmtInt(d.tokens_out)),
    el("td", { class: "num" }, fmtUsd(d.cost)),
    el("td", { class: "num" }, fmtDur(d.elapsed_s))));
  return el("div", { class: "stat-section" },
    el("h2", {}, title),
    el("div", { class: "table-wrap" },
      el("table", { class: "stat-table" },
        el("thead", {}, head), el("tbody", {}, ...body))));
}

// Monthly spend by routine — the durable series (workflow-usage stream, survives run
// retention): one row per routine, one column per month, tokens + cost per cell, a
// growth chip when the latest month runs >20% over the one before.
function monthlySection(monthly) {
  const months = (monthly?.months || []).slice(-6);
  const rows = Object.entries(monthly?.by_routine || {});
  if (!months.length || !rows.length) return null;
  const latest = months[months.length - 1];
  const prev = months[months.length - 2];
  const cell = (c) => (c ? `${fmtNum(c.tokens)} · ${fmtUsd(c.cost)}` : NBSP);
  const head = el("tr", {}, el("th", {}, "routine"),
    ...months.map((m) => el("th", { class: "num" }, m)),
    el("th", {}, "trend"));
  const body = rows.map(([slug, cells]) => {
    const cur = cells[latest];
    const before = prev ? cells[prev] : null;
    const growing = cur && before && cur.tokens > before.tokens * 1.2;
    const shrinking = cur && before && cur.tokens < before.tokens * 0.8;
    return el("tr", {},
      el("td", {}, slug),
      ...months.map((m) => el("td", { class: "num" }, cell(cells[m]))),
      el("td", {}, growing ? el("span", { class: "chip partial" }, "↑ growing")
        : shrinking ? el("span", { class: "chip ok" }, "↓ shrinking")
        : before && cur ? el("span", { class: "chip bare" }, "→ steady") : NBSP));
  });
  return el("div", { class: "stat-section" },
    el("h2", {}, "Monthly spend by routine"),
    el("div", { class: "sub" },
      "tokens · cost per calendar month, from the durable usage stream — unlike the tables above, this survives run retention"),
    el("div", { class: "table-wrap" },
      el("table", { class: "stat-table" }, el("thead", {}, head), el("tbody", {}, ...body))));
}

export async function render(view) {
  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("div", { class: "kicker" }, "console / analytics"),
      el("h1", {}, "Stats"),
      el("div", { class: "sub" }, "time, tokens & cost across every routine, conversation & endpoint")),
    el("div", { class: "row" }, el("button", { class: "btn small", onclick: () => load() }, "↻ refresh"))));

  const body = el("div", {});
  body.append(skeleton());
  view.append(body);

  async function load() {
    body.replaceChildren(skeleton());
    let agg;
    try {
      agg = await api("/api/stats");
    } catch (err) {
      body.replaceChildren(emptyState("✕", "stats unavailable", err.message));
      return;
    }
    const t = agg.totals || {};
    if (!t.runs) {
      body.replaceChildren(emptyState("◌", "no runs yet", "usage stats appear once routines have run"));
      return;
    }
    const parts = [];

    // ---- headline cards ---------------------------------------------------
    const successPct = t.success_rate == null ? "—" : Math.round(t.success_rate * 100) + "%";
    parts.push(el("div", { class: "stat-cards" },
      card("total runs", fmtInt(t.runs), `${fmtInt(t.routines)} routines · ${fmtInt(t.conversations)} conversations`),
      card("tokens", fmtNum(tokensOf(t)), `${fmtNum(t.tokens_in)} in · ${fmtNum(t.tokens_out)} out`),
      card("cost", fmtUsd(t.cost), "provider-reported"),
      card("compute time", fmtDur(t.elapsed_s), "summed wall-clock"),
      card("success rate", successPct, "finished ÷ graded")));

    // ---- configurable charts (metric × grouping × range × form, persisted) ----
    parts.push(chartsSection(agg.runs || []));

    // ---- monthly spend (durable series) ------------------------------------
    parts.push(monthlySection(agg.monthly));

    // ---- slice tables -----------------------------------------------------
    parts.push(sliceTable("By routine / conversation", agg.by_routine, "name", [
      { label: "kind", get: (d) => d.kind },
      { label: "endpoint", get: (d) => d.endpoint },
      { label: "model", get: (d) => d.model },
    ]));
    parts.push(sliceTable("By model", agg.by_model, "model"));
    parts.push(sliceTable("By endpoint", agg.by_endpoint, "endpoint"));
    parts.push(sliceTable("By kind", agg.by_kind, "kind"));

    // ---- run outcomes -----------------------------------------------------
    const states = Object.entries(agg.by_state || {});
    if (states.length) {
      parts.push(el("div", { class: "stat-section" },
        el("h2", {}, "run outcomes"),
        el("div", { class: "chip-row" },
          ...states.map(([s, n]) => el("span", { class: `chip chip-${s}` }, `${s}: ${fmtInt(n)}`)))));
    }

    parts.push(el("div", { class: "stat-foot" },
      `generated ${agg.generated || ""} · source: each run's status.json (no cache)`));

    body.replaceChildren(...parts.filter(Boolean));
  }

  await load();
  return null;
}
