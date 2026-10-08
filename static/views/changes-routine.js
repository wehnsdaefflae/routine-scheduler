// One routine's DEVELOPMENT view (#/changes/<slug>, rail group Develop): what changed in it and
// whether its runs got better (GET /api/changes/<slug>), which model served it how well, and its
// Recipe health — the recipe-version table, the regression flag with its roll-back, and the
// cautions' tallies (views/routine-health.js, moved here from the production routine page).
//
//   Changes      — newest first, one row each: when · what changed · runs before → after · the
//                  verdict · three tiny marks for correctness / completeness / effectiveness,
//                  the engine releases inside the windows as a faint note. A row opens onto its
//                  signal table: each signal's two sides and a ▲▼ coloured only by better/worse.
//   Model fit    — the runs grouped by model × effort × deliberation × trial (model_fit.py).
//   Recipe health
//
// The production page (#/routine/<slug>) carries one line pointing here and nothing else of it.
// Re-read on this routine's finished run only, and only the newest read paints.

import { api } from "/static/api.js";
import { DIMENSIONS, dimMarks, fmtSignal, signalMark, verdictChip, whatChanged }
  from "/static/components/change-marks.js";
import { onRunFinished } from "/static/components/run-finished.js";
import { mountHealth } from "/static/views/routine-health.js";
import { asButton, el, emptyState, fmtNum, skeleton, when } from "/static/util.js";

const th = (label, title = "", cls = "") => el("th", { title: title || null, class: cls || null }, label);
const none = (text) => el("div", { class: "dev-none faint small" }, text);

function signalTable(signals) {
  const rows = [];
  for (const dim of DIMENSIONS) {
    rows.push(el("tr", { class: "subhead" }, el("td", { colspan: 3 }, dim)));
    for (const s of signals.filter((x) => x.dimension === dim)) {
      rows.push(el("tr", { "data-signal": s.name },
        el("td", { class: "inline" }, s.label || s.name),
        el("td", { class: "num inline" },
          fmtSignal(s.name, s.before), el("span", { class: "faint" }, " → "), fmtSignal(s.name, s.after)),
        el("td", { class: "num inline" }, signalMark(s))));
    }
  }
  return el("table", { class: "list stack sig-table" },
    el("thead", {}, el("tr", {}, th("signal"), th("before → after", "the runs before the change → the runs after it (a median for tokens, turns and minutes; a per-run mean or a share otherwise)", "num"), th(""))),
    el("tbody", {}, ...rows));
}

function changesTable(changes, open) {
  const rows = [];
  for (const ch of changes) {
    const key = ch.run_id || ch.at;
    const isOpen = open.has(key);
    const detail = el("tr", { class: "chg-detail", hidden: !isOpen },
      el("td", { colspan: 5 }, signalTable(ch.signals || [])));
    const tri = el("span", { class: "tri" }, isOpen ? "▾" : "▸");
    const toggle = () => {
      const now = detail.hidden;
      detail.hidden = !now;
      now ? open.add(key) : open.delete(key);
      tri.textContent = now ? "▾" : "▸";
      head.setAttribute("aria-expanded", String(now));
    };
    const head = el("span", { class: "chg-toggle", ...asButton(toggle), "aria-expanded": String(isOpen),
                              title: "show the signals this verdict was judged on" },
      tri, when(ch.at, { mode: "rel" }));
    const engines = ch.engines || [];
    rows.push(el("tr", { class: "chg-row", "data-change": key,
                         onclick: (e) => { if (!e.target.closest("a")) toggle(); } },
      el("td", { class: "inline" }, head),
      el("td", {}, whatChanged(ch.what),
        engines.length ? el("div", { class: "chg-engines faint", title: "engine releases that ran inside these windows" },
          `engine ${engines.join(" · ")}`) : null),
      el("td", { class: "num inline", "data-label": "runs",
                 title: `${ch.runs_before} runs before → ${ch.runs_after} after` },
        `${ch.runs_before} → ${ch.runs_after}`),
      el("td", { class: "inline" }, verdictChip(ch.verdict)),
      el("td", { class: "inline" }, dimMarks(ch.dimensions))), detail);
  }
  return el("div", { class: "tablewrap" }, el("table", { class: "list stack chg-table" },
    el("thead", {}, el("tr", {}, th("when"), th("what changed"),
      th("runs", "runs judged before → after the change", "num"), th("verdict"),
      th("c · c · e", "correctness · completeness · effectiveness: + better, − worse, ± mixed, · same"))),
    el("tbody", {}, ...rows)));
}

const pct = (v) => (v == null ? "—" : `${Math.round(v * 100)}%`);
const dec = (v) => (v == null ? "—" : String(Math.round(v * 100) / 100));

function modelFit(groups) {
  if (!groups.length) return none("no fingerprinted run yet");
  return el("div", { class: "tablewrap" }, el("table", {
    class: "list stack fit-table",
    title: "runs grouped by what served them — comparable once the routine has run on more than one" },
  el("thead", {}, el("tr", {}, th("model"), th("effort"), th("deliberation"), th("trial"),
    th("runs", "", "num"), th("met", "share of owed lines met", "num"),
    th("failed", "share of runs failed or aborted", "num"),
    th("disputed", "claims disputed per run", "num"),
    th("interventions", "your interventions per run", "num"),
    th("tokens", "median per run", "num"), th("turns", "median per run", "num"),
    th("minutes", "median per run", "num"))),
  el("tbody", {}, ...groups.map((g) => el("tr", { "data-model": g.model },
    el("td", { class: "mono inline", title: g.last ? `last run ${g.last}` : "" }, g.model || "—"),
    el("td", { class: "mono inline", "data-label": "effort" }, g.effort || "—"),
    el("td", { class: "mono inline", "data-label": "delib." }, g.deliberation || "—"),
    el("td", { class: "mono inline", "data-label": "trial" }, g.trial || "—"),
    el("td", { class: "num inline", "data-label": "runs" }, String(g.runs)),
    el("td", { class: "num inline", "data-label": "met" }, pct(g.met_rate)),
    el("td", { class: "num inline", "data-label": "failed" }, pct(g.failed)),
    el("td", { class: "num inline", "data-label": "disputed" }, dec(g.disputed)),
    el("td", { class: "num inline", "data-label": "interv." }, dec(g.interventions)),
    el("td", { class: "num inline", "data-label": "tokens" }, g.tokens == null ? "—" : fmtNum(g.tokens)),
    el("td", { class: "num inline", "data-label": "turns" }, dec(g.turns)),
    el("td", { class: "num inline", "data-label": "min" },
      g.elapsed_s == null ? "—" : (g.elapsed_s / 60).toFixed(1)))))));
}

export async function render(view, slug) {
  view.classList.add("dev-view");
  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("div", { class: "kicker dev" }, "development"),
      el("h1", { class: "mono" }, slug),
      el("div", { class: "sub" }, "What changed in this routine and whether its runs got better. ",
        el("a", { href: `#/routine/${encodeURIComponent(slug)}` }, "The routine itself →")))));
  const changesBox = el("div", {}, skeleton(["100%", "100%", "70%"]));
  const fitBox = el("div", {}, skeleton(["100%", "60%"]));
  const healthBox = el("div", {}, skeleton(["60%", "90%"]));
  const changesHead = el("h2", { id: "sec-changes" }, "Changes");
  view.append(changesHead, changesBox,
    el("h2", { id: "sec-model-fit" }, "Model fit"), fitBox,
    el("h2", { id: "sec-recipe-health" }, "Recipe health"), healthBox);
  const health = mountHealth(healthBox, slug);

  const open = new Set();   // the rows a reader opened survive a live re-read
  let alive = true, seq = 0;
  async function load() {
    const mine = ++seq;
    let data;
    try { data = await api(`/api/changes/${encodeURIComponent(slug)}`); }
    catch (err) {
      if (alive && mine === seq) {
        changesBox.replaceChildren(emptyState("✕", `Couldn't load ${slug}'s changes`, err.message));
        fitBox.replaceChildren();
      }
      return;
    }
    if (!alive || mine !== seq) return;
    const changes = data.changes || [];
    changesHead.textContent = changes.length ? `Changes · ${changes.length}` : "Changes";
    changesBox.replaceChildren(changes.length ? changesTable(changes, open)
      : none("no measured change yet — one is found once runs on both sides of it carry a fingerprint"));
    fitBox.replaceChildren(modelFit(data.model_fit || []));
  }

  await load();
  const stop = onRunFinished(() => { load().catch(() => {}); health.reload(); }, { slug });
  return () => { alive = false; stop(); health.dispose(); };
}
