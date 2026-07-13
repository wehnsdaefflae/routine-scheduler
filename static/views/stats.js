// Stats: usage analytics across every run in the routines + conversations homes — time,
// tokens, and cost rolled up by routine, model, endpoint, day, kind, and run-state
// (served by /api/stats → rsched.stats.aggregate). Read-only; the filesystem is the truth,
// so a refresh always reflects the live state. Diagrams are dependency-free inline CSS bars.

import { api } from "/static/api.js";
import { el, emptyState, skeleton, toast } from "/static/util.js";

const NBSP = " ";

function fmtInt(n) {
  return (n || 0).toLocaleString("en-US");
}
function fmtTokens(n) {
  n = n || 0;
  if (n >= 1e9) return (n / 1e9).toFixed(2) + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(n);
}
function fmtCost(c) {
  return "$" + (c || 0).toFixed(c && c < 1 ? 4 : 2);
}
function fmtDur(s) {
  s = Math.round(s || 0);
  if (s >= 3600) return (s / 3600).toFixed(1) + "h";
  if (s >= 60) return (s / 60).toFixed(1) + "m";
  return s + "s";
}
function tokensOf(d) {
  return (d.tokens_in || 0) + (d.tokens_out || 0);
}

function card(label, value, sub) {
  return el("div", { class: "stat-card" },
    el("div", { class: "stat-value" }, value),
    el("div", { class: "stat-label" }, label),
    sub ? el("div", { class: "stat-sub" }, sub) : null);
}

// A labelled horizontal bar row, width proportional to `value`/`max`.
function barRow(label, value, max, valueText) {
  const pct = max > 0 ? Math.max(2, Math.round((value / max) * 100)) : 0;
  return el("div", { class: "bar-row" },
    el("div", { class: "bar-label", title: label }, label),
    el("div", { class: "bar-track" },
      el("div", { class: "bar-fill", style: `width:${pct}%` })),
    el("div", { class: "bar-value" }, valueText));
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
    el("td", { class: "num" }, fmtCost(d.cost)),
    el("td", { class: "num" }, fmtDur(d.elapsed_s))));
  return el("div", { class: "stat-section" },
    el("h2", {}, title),
    el("div", { class: "table-wrap" },
      el("table", { class: "stat-table" },
        el("thead", {}, head), el("tbody", {}, ...body))));
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
      body.replaceChildren(emptyState("stats unavailable", err.message));
      return;
    }
    const t = agg.totals || {};
    if (!t.runs) {
      body.replaceChildren(emptyState("no runs yet", "usage stats appear once routines have run"));
      return;
    }
    const parts = [];

    // ---- headline cards ---------------------------------------------------
    const successPct = t.success_rate == null ? "—" : Math.round(t.success_rate * 100) + "%";
    parts.push(el("div", { class: "stat-cards" },
      card("total runs", fmtInt(t.runs), `${fmtInt(t.routines)} routines · ${fmtInt(t.conversations)} conversations`),
      card("tokens", fmtTokens(tokensOf(t)), `${fmtTokens(t.tokens_in)} in · ${fmtTokens(t.tokens_out)} out`),
      card("cost", fmtCost(t.cost), "provider-reported"),
      card("compute time", fmtDur(t.elapsed_s), "summed wall-clock"),
      card("success rate", successPct, "finished ÷ graded")));

    // ---- tokens per day bar chart ----------------------------------------
    const days = Object.entries(agg.by_day || {});
    if (days.length) {
      const max = Math.max(...days.map(([, d]) => tokensOf(d)));
      parts.push(el("div", { class: "stat-section" },
        el("h2", {}, "tokens per day"),
        el("div", { class: "bar-chart" },
          ...days.map(([day, d]) => barRow(day, tokensOf(d), max, fmtTokens(tokensOf(d)))))));
    }

    // ---- cost per endpoint bar chart (only when some cost is reported) ----
    const eps = Object.entries(agg.by_endpoint || {});
    const anyCost = eps.some(([, d]) => d.cost > 0);
    if (eps.length && anyCost) {
      const max = Math.max(...eps.map(([, d]) => d.cost || 0));
      parts.push(el("div", { class: "stat-section" },
        el("h2", {}, "cost per endpoint"),
        el("div", { class: "bar-chart" },
          ...eps.filter(([, d]) => d.cost > 0)
            .map(([ep, d]) => barRow(ep, d.cost, max, fmtCost(d.cost))))));
    }

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
