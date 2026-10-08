// One routine's DEVELOPMENT view (#/changes/<slug>, rail group Develop): what changed in it and
// whether its runs got better (GET /api/changes/<slug>), which model served it how well, and its
// Recipe health — the recipe-version table, the regression flag with its roll-back, and the
// cautions' tallies (views/routine-health.js, moved here from the production routine page).
//
//   Weeks        — the routine's own weeks as small multiples (components/sparkline.js): tokens
//                  and turns against its usual, lines met, interventions per run.
//   Changes      — newest first, one row each: when · what changed · runs before → after · the
//                  verdict · three tiny marks for correctness / completeness / effectiveness,
//                  the engine releases inside the windows as a faint note (a range once there
//                  are more than two). A row opens onto its signal table: each signal's two
//                  sides and a ▲▼ coloured only by better/worse.
//                  The history is months long and most of it changed faster than it ran, so
//                  the list is compressed three ways: consecutive `too few runs` changes fold
//                  into ONE row that opens onto them, a change revising more than three rules
//                  names the count and opens onto the list, and only the newest ten rows show
//                  until "show older".
//   Model fit    — the runs grouped by model × effort × deliberation × trial (model_fit.py); a
//                  rebuilt run's catalog name and effort are unknown, so its provider id stands in.
//   Recipe health
//
// "≈" marks a reading drawn on runs rebuilt from history. The production page (#/routine/<slug>)
// carries one line pointing here and nothing else of it. Re-read on this routine's finished run
// only, and only the newest read paints; whatever the reader opened stays open across it.

import { api } from "/static/api.js";
import { DIMENSIONS, dimMarks, fmtSignal, signalMark, verdictChip, whatChanged }
  from "/static/components/change-marks.js";
import { foldEntries, rebuiltMark } from "/static/components/change-folds.js";
import { onRunFinished } from "/static/components/run-finished.js";
import { fmt, sparkStrip } from "/static/components/sparkline.js";
import { mountHealth } from "/static/views/routine-health.js";
import { asButton, el, emptyState, fmtNum, skeleton, toDate, when } from "/static/util.js";

const th = (label, title = "", cls = "") => el("th", { title: title || null, class: cls || null }, label);
const none = (text) => el("div", { class: "dev-none faint small" }, text);
const MANY_RULES = 3;
const KEEP = 10;

const ROUTINE_SERIES = [
  { key: "tokens", label: "tokens ×", log: true, lo: 0.25, hi: 4, ref: 1,
    title: "tokens per run against this routine's own median — ×1.00 is usual (log scale, ×0.25–×4)",
    pick: (w) => w.signals?.tokens, format: fmt.ratio },
  { key: "turns", label: "turns ×", log: true, lo: 0.25, hi: 4, ref: 1,
    title: "turns per run against this routine's own median — ×1.00 is usual (log scale, ×0.25–×4)",
    pick: (w) => w.signals?.turns, format: fmt.ratio },
  { key: "met_rate", label: "lines met", title: "share of owed Done-when lines met",
    pick: (w) => w.signals?.met_rate, format: fmt.share, hi: 1, minSpan: 0.2 },
  { key: "interventions", label: "interventions", title: "your interventions per run",
    pick: (w) => w.signals?.interventions, format: fmt.per, lo: 0, minSpan: 0.5 },
];

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

/** What changed; more than three rule entries read as a count that opens onto them. */
function whatCell(what, key, opened) {
  const rules = what.filter((w) => w.kind === "rule");
  if (rules.length <= MANY_RULES) return whatChanged(what);
  const rest = what.filter((w) => w.kind !== "rule");
  const added = rules.filter((w) => !w.from).length, removed = rules.filter((w) => !w.to).length;
  const parts = [added ? `+${added} added` : "", removed ? `−${removed} removed` : "",
                 `${rules.length - added - removed} revised`].filter(Boolean).join(" · ");
  const ruleKey = `rules:${key}`;
  const list = el("div", { class: "chg-rules", hidden: !opened.has(ruleKey) }, whatChanged(rules));
  const btn = el("button", { type: "button", class: "chg-rules-toggle", "data-rules": rules.length,
                             "aria-expanded": String(opened.has(ruleKey)), title: `${parts} — show them` },
    `${rules.length} rules revised`);
  btn.addEventListener("click", () => {
    list.hidden = !list.hidden;
    if (list.hidden) opened.delete(ruleKey); else opened.add(ruleKey);
    btn.setAttribute("aria-expanded", String(!list.hidden));
  });
  return el("span", { class: "chg-what" }, rest.length ? [whatChanged(rest), el("span", { class: "faint" }, " · ")] : null,
    btn, list);
}

const short = (v) => String(v).replace(/\.0$/, "");

function enginesNote(engines) {
  if (!engines.length) return null;
  const text = engines.length > 2
    ? `engine ${short(engines[0])}–${short(engines.at(-1))} (${engines.length} releases)`
    : `engine ${engines.join(" · ")}`;
  return el("div", { class: "chg-engines faint",
                     title: `engine releases that ran inside these windows: ${engines.join(", ")}` }, text);
}

/** One change: its row and its (folded) signal detail — the two rows one entry. */
function changeRows(ch, opened) {
  const key = ch.run_id || ch.at;
  const isOpen = opened.has(key);
  const detail = el("tr", { class: "chg-detail", hidden: !isOpen },
    el("td", { colspan: 5 }, signalTable(ch.signals || [])));
  const tri = el("span", { class: "tri" }, isOpen ? "▾" : "▸");
  const toggle = () => {
    const now = detail.hidden;
    detail.hidden = !now;
    if (now) opened.add(key); else opened.delete(key);
    tri.textContent = now ? "▾" : "▸";
    head.setAttribute("aria-expanded", String(now));
  };
  const head = el("span", { class: "chg-toggle", ...asButton(toggle), "aria-expanded": String(isOpen),
                            title: "show the signals this verdict was judged on" },
    tri, when(ch.at, { mode: "rel" }));
  const row = el("tr", { class: `chg-row${ch.verdict === "too few runs" ? " quiet" : ""}`, "data-change": key,
                         onclick: (e) => { if (!e.target.closest("a, button")) toggle(); } },
    el("td", { class: "inline" }, head),
    el("td", {}, whatCell(ch.what || [], key, opened), enginesNote(ch.engines || [])),
    el("td", { class: "num inline", "data-label": "runs",
               title: `${ch.runs_before} runs before → ${ch.runs_after} after` },
      `${ch.runs_before} → ${ch.runs_after}`),
    el("td", { class: "inline" }, el("span", { class: "v-line" },
      verdictChip(ch.verdict), ch.reconstructed ? rebuiltMark() : null)),
    el("td", { class: "inline" }, dimMarks(ch.dimensions)));
  return [row, detail];
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function day(v) {
  const d = toDate(v);
  return d ? `${d.getDate()} ${MONTHS[d.getMonth()]}` : "";
}

/** Consecutive `too few runs` changes as ONE row that opens onto them. `group` is newest first;
 *  it is keyed by its OLDEST change, which a newer change joining the run does not move. */
function groupRows(group, opened) {
  const key = `group:${group.at(-1).run_id || group.at(-1).at}`;
  const members = group.flatMap((ch) => changeRows(ch, opened));
  for (const r of members) r.classList.add("in-group");
  const tri = el("span", { class: "tri" });
  const paint = () => {
    const open = opened.has(key);
    for (const r of members) r.classList.toggle("collapsed", !open);
    tri.textContent = open ? "▾" : "▸";
    head.setAttribute("aria-expanded", String(open));
  };
  const toggle = () => { if (opened.has(key)) opened.delete(key); else opened.add(key); paint(); };
  const first = day(group.at(-1).at), last = day(group[0].at);
  const head = el("span", { class: "chg-toggle", ...asButton(toggle),
                            title: "each came before either side had enough runs to judge — show them" },
    tri, el("span", { class: "v-glyph faint", "aria-hidden": "true" }, "∅"),
    `${group.length} changes came faster than it ran`,
    el("span", { class: "faint mono" }, ` · ${first === last ? first : `${first}–${last}`}`),
    group.some((ch) => ch.reconstructed) ? rebuiltMark() : null);
  const row = el("tr", { class: "chg-group", "data-group": group.length,
                         onclick: (e) => { if (!e.target.closest("a, button")) toggle(); } },
    el("td", { colspan: 5 }, head));
  paint();
  return [row, ...members];
}

function changesTable(changes, opened) {
  const entries = [];
  let run = [];
  const flush = () => {
    if (run.length > 1) entries.push(groupRows(run, opened));
    else if (run.length) entries.push(changeRows(run[0], opened));
    run = [];
  };
  for (const ch of changes) {
    if (ch.verdict === "too few runs") { run.push(ch); continue; }
    flush();
    entries.push(changeRows(ch, opened));
  }
  flush();
  const older = entries.length - KEEP;
  return el("div", { class: "tablewrap" }, el("table", { class: "list stack chg-table" },
    el("thead", {}, el("tr", {}, th("when"), th("what changed"),
      th("runs", "runs judged before → after the change", "num"), th("verdict"),
      th("c · c · e", "correctness · completeness · effectiveness: + better, − worse, ± mixed, · same"))),
    el("tbody", {}, ...foldEntries(entries, {
      keep: KEEP, key: "changes", state: opened, closed: `show ${older} older`, opened: "show newest only" }))));
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
  el("tbody", {}, ...groups.map((g) => el("tr", { "data-model": g.model || g.model_id || "" },
    el("td", { class: "mono inline",
               title: [g.model_id ? `provider id ${g.model_id}` : "", g.last ? `last run ${g.last}` : ""]
                 .filter(Boolean).join(" · ") },
      g.model || g.model_id || "—"),
    el("td", { class: "mono inline", "data-label": "effort" }, g.effort || "—"),
    el("td", { class: "mono inline", "data-label": "delib." }, g.deliberation || "—"),
    el("td", { class: "mono inline", "data-label": "trial" }, g.trial || "—"),
    el("td", { class: "num inline", "data-label": "runs" }, String(g.runs),
      g.rebuilt ? rebuiltMark(g.rebuilt) : null),
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
  const weeksHead = el("h2", { id: "sec-weeks", hidden: true }, "Weeks");
  const weeksBox = el("div", {});
  const changesBox = el("div", {}, skeleton(["100%", "100%", "70%"]));
  const fitBox = el("div", {}, skeleton(["100%", "60%"]));
  const healthBox = el("div", {}, skeleton(["60%", "90%"]));
  const changesHead = el("h2", { id: "sec-changes" }, "Changes");
  view.append(weeksHead, weeksBox, changesHead, changesBox,
    el("h2", { id: "sec-model-fit" }, "Model fit"), fitBox,
    el("h2", { id: "sec-recipe-health" }, "Recipe health"), healthBox);
  const health = mountHealth(healthBox, slug);

  const opened = new Set();   // the rows, groups and folds a reader opened survive a live re-read
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
    const strip = sparkStrip(data.timeline || [], ROUTINE_SERIES);
    weeksHead.hidden = !strip;
    weeksBox.replaceChildren(...(strip ? [strip] : []));
    changesHead.textContent = changes.length ? `Changes · ${changes.length}` : "Changes";
    changesBox.replaceChildren(changes.length ? changesTable(changes, opened)
      : none("no measured change yet — one is found once runs on both sides of it carry a fingerprint"));
    fitBox.replaceChildren(modelFit(data.model_fit || []));
  }

  await load();
  const stop = onRunFinished(() => { load().catch(() => {}); health.reload(); }, { slug });
  return () => { alive = false; stop(); health.dispose(); };
}
