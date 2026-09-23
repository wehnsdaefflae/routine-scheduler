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

// Prompt-cache read share: reads ÷ all cache traffic. The reads alone read as healthy on a
// broken transport (the static prefix keeps hitting while the conversation is re-written
// every turn at 12.5x the price), so this ratio — not the cached column — is the reading.
// null = the endpoint reports no cache traffic at all, which is absence, not a bad share.
function cacheShare(d) {
  const total = (d.tokens_cached || 0) + (d.tokens_cache_write || 0);
  return total ? (d.tokens_cached || 0) / total : null;
}

function cacheCell(d) {
  const share = cacheShare(d);
  if (share == null) return el("td", { class: "num muted" }, NBSP);
  const pct = Math.round(share * 100) + "%";
  const title = `${fmtNum(d.tokens_cached || 0)} read · ${fmtNum(d.tokens_cache_write || 0)} written`;
  return el("td", { class: share < 0.5 ? "num cache-low" : "num", title }, pct);
}

function card(label, value, sub) {
  // the ONE stat-tile system (.stats/.stat — shared with the log view's strip)
  return el("div", { class: "stat" },
    el("div", { class: "v" }, value),
    el("div", { class: "l" }, label),
    sub ? el("div", { class: "s" }, sub) : null);
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

// Show the first `keep` rows and hide the rest behind ONE row that reveals them. Stats is a
// page of KPI tiles and two charts followed, today, by 20 000px of rows nobody scans — 172
// monthly rows, 196 util rows. Every row is still here and still one click away; the page just
// stops spending its height on them before anyone asks. The reveal row goes LAST, because a
// hidden <tr> takes no space and the button therefore sits under what is shown.
function foldRows(rows, keep, label) {
  if (rows.length <= keep) return rows;
  const rest = rows.slice(keep);
  for (const r of rest) r.hidden = true;
  const btn = el("button", { class: "btn small ghost" }, `show all ${rows.length} ${label}`);
  btn.onclick = () => {
    const opening = rest[0].hidden;
    for (const r of rest) r.hidden = !opening;
    btn.textContent = opening ? `show fewer ${label}` : `show all ${rows.length} ${label}`;
  };
  return [...rows, el("tr", { class: "fold-row" }, el("td", { colspan: "99" }, btn))];
}

// The frame every stat table on this page shares: a titled section, an optional sub-line
// explaining where the numbers come from, and one horizontally scrollable table. `sub` takes a
// string, an array of strings (el flattens its children), or null when the title already says
// everything — el drops the null child, so there is no empty .sub div to style around.
const statSection = (title, sub, head, body) => el("div", { class: "stat-section" },
  el("h2", {}, title),
  sub ? el("div", { class: "sub" }, sub) : null,
  el("div", { class: "tablewrap boxed" },
    el("table", { class: "stat-table" },
      el("thead", {}, head), el("tbody", {}, ...body))));

// Recipe length by routine (F371): how much INSTRUCTION each routine carries — one
// violet bar per routine (deliberately not a usage-series color: this is prose mass,
// not spend) plus a trend chip against the recipe as committed ~30 days ago (from each
// routine dir's own git history). Sits beside the token charts so "how much it costs"
// and "how much it is told" read together.
function recipeSection(recipes) {
  const rows = Object.entries(recipes?.by_routine || {});
  if (!rows.length) return null;
  rows.sort((a, b) => b[1].chars - a[1].chars);
  const max = Math.max(...rows.map(([, d]) => d.chars), 1);
  const days = recipes.trend_days || 30;
  const trendChip = (d) => {
    const base = d.chars_baseline;
    if (!base) return NBSP;                       // no git history that far back
    if (d.chars > base * 1.05) return el("span", { class: "chip partial", title: `${fmtInt(base)} chars ${days}d ago` }, "↑ growing");
    if (d.chars < base * 0.95) return el("span", { class: "chip ok", title: `${fmtInt(base)} chars ${days}d ago` }, "↓ shrinking");
    return el("span", { class: "chip bare", title: `${fmtInt(base)} chars ${days}d ago` }, "→ steady");
  };
  const body = rows.map(([slug, d]) => el("tr", {},
    el("td", {}, el("a", { href: `#/routine/${slug}` }, slug)),
    el("td", { class: "recipe-bar-cell" },
      el("div", { class: "recipe-bar", style: `width:${Math.max(1, Math.round((d.chars / max) * 100))}%` })),
    el("td", { class: "num" }, fmtInt(d.chars)),
    el("td", {}, trendChip(d))));
  return statSection("Recipe length by routine",
    `instruction mass (main.md + stages/ + tuning.yaml, chars) with its trend vs the recipe ${days} days ago — from each routine's git history`,
    el("tr", {},
      el("th", {}, "routine"),
      el("th", { style: "width:40%" }, "length"),
      el("th", { class: "num" }, "chars"),
      el("th", {}, "trend")),
    body);
}

// Deep link for a routine/conversation row — null (plain text) when the home is unknown.
const routineHref = (slug, kind) =>
  kind === "routine" ? `#/routine/${slug}`
    : kind === "conversation" ? `#/conversations/${slug}` : null;

// A table over a {key: metrics} slice, sorted by total tokens desc.
function sliceTable(title, slice, keyLabel, extraCols, link) {
  const rows = Object.entries(slice || {});
  if (!rows.length) return null;
  const head = el("tr", {},
    el("th", {}, keyLabel),
    ...(extraCols || []).map((c) => el("th", {}, c.label)),
    el("th", { class: "num" }, "runs"),
    el("th", { class: "num" }, "tokens in"),
    el("th", { class: "num" }, "tokens out"),
    el("th", { class: "num", title: "prompt-cache read share: reads ÷ (reads + writes). Blank = this endpoint reports no cache traffic" }, "cache"),
    el("th", { class: "num" }, "cost"),
    el("th", { class: "num" }, "time"));
  const body = rows.map(([k, d]) => el("tr", {},
    el("td", {}, link?.(k, d) ? el("a", { href: link(k, d) }, k) : k),
    ...(extraCols || []).map((c) => el("td", {}, c.get(d) || NBSP)),
    el("td", { class: "num" }, fmtInt(d.runs)),
    el("td", { class: "num" }, fmtInt(d.tokens_in)),
    el("td", { class: "num" }, fmtInt(d.tokens_out)),
    cacheCell(d),
    el("td", { class: "num" }, fmtUsd(d.cost)),
    el("td", { class: "num" }, fmtDur(d.elapsed_s))));
  return statSection(title, null, head, body);
}

// Monthly spend by routine — the durable series (workflow-usage stream, survives run
// retention): one row per routine, one column per month, tokens + cost per cell, a
// growth chip when the latest month runs >20% over the one before.
function monthlySection(monthly, kinds) {
  const months = (monthly?.months || []).slice(-6);
  const rows = Object.entries(monthly?.by_routine || {});
  if (!months.length || !rows.length) return null;
  const latest = months[months.length - 1];
  const prev = months[months.length - 2];
  const cell = (c) => (c ? `${fmtNum(c.tokens)} · ${fmtUsd(c.cost)}` : NBSP);
  const head = el("tr", {}, el("th", {}, "routine"),
    ...months.map((m) => el("th", { class: "num" }, m)),
    el("th", {}, "trend"));
  const rowFor = ([slug, cells]) => {
    const cur = cells[latest];
    const before = prev ? cells[prev] : null;
    const growing = cur && before && cur.tokens > before.tokens * 1.2;
    const shrinking = cur && before && cur.tokens < before.tokens * 0.8;
    const href = routineHref(slug, kinds?.[slug]);
    return el("tr", {},
      el("td", {}, href ? el("a", { href }, slug) : slug),
      ...months.map((m) => el("td", { class: "num" }, cell(cells[m]))),
      el("td", {}, growing ? el("span", { class: "chip partial" }, "↑ growing")
        : shrinking ? el("span", { class: "chip ok" }, "↓ shrinking")
        : before && cur ? el("span", { class: "chip bare" }, "→ steady") : NBSP));
  };
  // A CONVERSATION is a one-off with a timestamp for a name, and the fleet has a hundred of
  // them: as peers of the routines they made this table a list of `c-2026MMDD-HHMMSS` ids and
  // buried the twenty rows a reader came for. They subtotal into one row that expands.
  const isConv = ([slug]) => kinds?.[slug] === "conversation";
  const convs = rows.filter(isConv);
  const body = rows.filter((r) => !isConv(r)).map(rowFor);
  if (convs.length) {
    const totals = {};
    for (const [, cells] of convs)
      for (const m of months) {
        const c = cells[m];
        if (!c) continue;
        totals[m] = totals[m] || { tokens: 0, cost: 0 };
        totals[m].tokens += c.tokens || 0;
        totals[m].cost += c.cost || 0;
      }
    body.push(...foldRows([el("tr", { class: "subtotal" },
      el("td", {}, `conversations · ${convs.length}`),
      ...months.map((m) => el("td", { class: "num" }, cell(totals[m]))),
      el("td", {}, NBSP)), ...convs.map(rowFor)], 1, "conversations"));
  }
  return statSection("Monthly spend by routine",
    "tokens · cost per calendar month, from the durable usage stream — unlike the tables above, this survives run retention",
    head, body);
}

// Per-util execution stats — every global util's life story: created / last revised
// (library git history), how often executed / successful / mis-called / permission-
// blocked, first & last execution (durable usage stream + a transcript backfill for
// pre-stream history). Honest about unknowns: no date = no git history, dashes = never
// seen; rejected/denied counting starts with the stream.
function utilsSection(u) {
  const rows = u?.utils || [];
  if (!rows.length) return null;
  const day = (iso) => (iso ? String(iso).slice(0, 10) : "—");
  const num = (n) => (n ? fmtInt(n) : "—");
  const head = el("tr", {},
    el("th", {}, "util"),
    el("th", {}, "created"),
    el("th", {}, "last revised"),
    el("th", { class: "num" }, "executed"),
    el("th", { class: "num", title: "exit 0" }, "ok"),
    el("th", { class: "num", title: "non-zero exit (util ran and failed)" }, "errors"),
    el("th", { class: "num", title: "exit 2 — called with bad arguments (argparse convention)" }, "syntax err"),
    el("th", { class: "num", title: "reserved util switched off / kind not permitted — rejected before running" }, "denied"),
    el("th", { class: "num", title: "malformed action (schema/field problems) — rejected before running" }, "rejected"),
    el("th", { class: "num", title: "called by a name that doesn't exist in the library" }, "missing"),
    el("th", {}, "first executed"),
    el("th", {}, "last executed"));
  const rowFor = (r) => {
    const okPct = r.executed ? ` (${Math.round((r.ok / r.executed) * 100)}%)` : "";
    return el("tr", {},
      // DELETED is a historical fact about a util, not a failure of one: 86 of the fleet's 196
      // rows carried it, which is 86 err-toned chips at rest on one page. Colour is state.
      el("td", { title: r.summary || "" }, r.name,
        r.in_library ? "" : el("span", { class: "chip disabled", style: "margin-left:6px" }, "deleted")),
      el("td", { class: "muted" }, day(r.created)),
      el("td", { class: "muted" }, day(r.revised)),
      el("td", { class: "num" }, r.executed ? fmtInt(r.executed) : "never"),
      el("td", { class: "num" }, r.executed ? fmtInt(r.ok) + okPct : "—"),
      el("td", { class: "num" }, num(r.error)),
      el("td", { class: "num" }, num(r.usage_error)),
      el("td", { class: "num" }, num(r.denied)),
      el("td", { class: "num" }, num(r.rejected)),
      el("td", { class: "num" }, num(r.missing)),
      el("td", { class: "muted" }, day(r.first_executed)),
      el("td", { class: "muted" }, day(r.last_executed)));
  };
  // Busiest first, and the utils the library no longer holds behind their own reveal — they are
  // history, and the question this table answers ("which util is unreliable?") is about the
  // ones that still exist.
  const live = rows.filter((r) => r.in_library).sort((a, b) => (b.executed || 0) - (a.executed || 0));
  const gone = rows.filter((r) => !r.in_library).sort((a, b) => (b.executed || 0) - (a.executed || 0));
  const body = [...foldRows(live.map(rowFor), 25, "utils"),
                ...foldRows(gone.map(rowFor), 0, "deleted utils")];
  return statSection("Global utils",
    ["per-util reliability: dates from the library's git history; counts from each run's usage record ",
     `(durable) plus ${fmtInt(u.backfill_runs || 0)} pre-stream runs backfilled from retained transcripts. `,
     "Denied/rejected calls never ran (caught at validation), so those counts begin with the stream."],
    head, body);
}

// Output compression by routine (docs/output-compression.md): what the optional compressor
// actually bought. `candidates` is every successful command output the mode let through,
// `attempts` the ones the compressor ran on, `applied` the previews that actually replaced
// an observation — and `rejected` is the half that has no upside at all: a result the
// engine's own verification refused, paid for in compressor time and thrown away. Savings
// are the recorded ESTIMATE (preview chars ÷ 4), never a billing reading.
function compressionSection(c) {
  const rows = c?.rows || [];
  const t = c?.totals || {};
  if (!rows.length) {
    return el("div", { class: "stat-section" },
      el("h2", {}, "Output compression by routine"),
      el("div", { class: "sub" }, "no counted run yet — the tally is written to the durable "
        + "usage stream when a run finishes, so the first finished run fills this in."));
  }
  const hit = t.attempts ? ` · ${Math.round((t.applied / t.attempts) * 100)}% of attempts landed` : "";
  const num = (n) => (n ? fmtInt(n) : "—");
  const head = el("tr", {},
    el("th", {}, "routine"),
    el("th", { title: "the routine's setting now — an empty row on 'off' is switched off, not ineligible" }, "mode"),
    el("th", { class: "num" }, "runs"),
    el("th", { class: "num", title: "successful command outputs the mode let through; most are too small or are neither JSON nor logs" }, "candidates"),
    el("th", { class: "num", title: "candidates the compressor actually ran on" }, "attempts"),
    el("th", { class: "num", title: "previews that replaced the observation the model read" }, "applied"),
    el("th", { class: "num", title: "estimated tokens saved by applied previews (preview chars ÷ 4) — an estimate, not billing" }, "~tokens saved"),
    el("th", { class: "num", title: "the compressor's result failed the engine's own verification (JSON that did not survive it, or an unusable result): the original was kept and the time is gone" }, "rejected"),
    el("th", { class: "num", title: "compressed fine, but no smaller complete preview than the existing capped one" }, "no gain"),
    el("th", { class: "num", title: "wall clock spent inside the compressor, whatever the outcome" }, "time"));
  const body = rows.map((r) => el("tr", {},
    el("td", {}, el("a", { href: `#/routine/${r.routine}` }, r.routine)),
    el("td", { class: "muted" }, r.mode || "—"),
    el("td", { class: "num" }, fmtInt(r.runs)),
    el("td", { class: "num" }, num(r.candidates)),
    el("td", { class: "num" }, num(r.attempts)),
    el("td", { class: "num" }, r.attempts
      ? `${fmtInt(r.applied)} (${Math.round((r.applied / r.attempts) * 100)}%)` : "—"),
    el("td", { class: "num" }, num(r.tokens_saved)),
    // more rejected than applied: this routine's outputs cost the compressor more than they bought
    el("td", { class: r.fallback > r.applied ? "num warn" : "num" }, num(r.fallback)),
    el("td", { class: "num" }, num(r.unchanged)),
    el("td", { class: "num" }, r.seconds ? fmtDur(Math.round(r.seconds)) : "—")));
  return statSection("Output compression by routine",
    [`${fmtInt(t.applied || 0)} previews applied of ${fmtInt(t.attempts || 0)} attempts${hit}, `,
     `~${fmtNum(t.tokens_saved || 0)} tokens saved for ${fmtDur(Math.round(t.seconds || 0))} of compressor time, `,
     `over ${fmtInt(c.records || 0)} counted runs since ${String(c.since || "").slice(0, 10)}. `,
     "From the durable usage stream; savings are the recorded estimate, not a billing reading."],
    head, body);
}

export async function render(view) {
  view.append(el("div", { class: "page-head" },
    el("div", {},
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
    parts.push(el("div", { class: "stats" },
      card("total runs", fmtInt(t.runs), `${fmtInt(t.routines)} routines · ${fmtInt(t.conversations)} conversations`),
      card("tokens", fmtNum(tokensOf(t)), `${fmtNum(t.tokens_in)} in · ${fmtNum(t.tokens_out)} out`),
      card("prompt cache", cacheShare(t) == null ? "—" : Math.round(cacheShare(t) * 100) + "%",
        `${fmtNum(t.tokens_cached)} read · ${fmtNum(t.tokens_cache_write)} written`),
      card("cost", fmtUsd(t.cost), "provider-reported"),
      card("compute time", fmtDur(t.elapsed_s), "summed wall-clock"),
      card("success rate", successPct, "finished ÷ graded")));

    // ---- configurable charts (metric × grouping × range × form, persisted) ----
    parts.push(chartsSection(agg.runs || []));

    // ---- recipe length (instruction mass + trend, beside the token charts) ----
    parts.push(recipeSection(agg.recipes));

    // ---- monthly spend (durable series) ------------------------------------
    parts.push(monthlySection(agg.monthly, Object.fromEntries(
      Object.entries(agg.by_routine || {}).map(([k, d]) => [k, d.kind]))));

    // ---- per-util execution stats -------------------------------------------
    parts.push(utilsSection(agg.utils));

    // ---- what the optional output compressor bought --------------------------
    parts.push(compressionSection(agg.compression));

    // ---- slice tables -----------------------------------------------------
    parts.push(sliceTable("By routine / conversation", agg.by_routine, "name", [
      { label: "kind", get: (d) => d.kind },
      { label: "endpoint", get: (d) => d.endpoint },
      { label: "model", get: (d) => d.model },
    ], (k, d) => routineHref(k, d.kind)));
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
