// Changes — the DEVELOPMENT view of the fleet (#/changes, rail group Develop). Did a change
// help? Every change is found from the runs' own fingerprints and judged on the runs before
// against the runs after it (readmodels/change_effects.py, served by GET /api/changes):
//
//   Releases               — each engine release, judged across the routines that held still
//                            around it: how many, their verdicts, the median token ratio and the
//                            met-rate change;
//   Model & rule changes   — one row per model / effort / rule change, with each routine's
//                            verdict;
//   Routines               — every routine with a measured change: how many, the newest verdict,
//                            and the way into its own development view (#/changes/<slug>).
//
// Production and development are kept apart (operator, 2026-10-08), and the console is text
// heavy already: tables of chips and mono numbers here, the explanations in tooltips. Re-read
// on a finished run only (components/run-finished.js), and only the newest read paints.

import { api } from "/static/api.js";
import { verdictChip, verdictCounts, whatText } from "/static/components/change-marks.js";
import { onRunFinished } from "/static/components/run-finished.js";
import { el, emptyState, skeleton, when } from "/static/util.js";

const th = (label, title = "", cls = "") => el("th", { title: title || null, class: cls || null }, label);

function table(cls, heads, rows) {
  return el("div", { class: "tablewrap" },
    el("table", { class: `list stack ${cls}` },
      el("thead", {}, el("tr", {}, ...heads)), el("tbody", {}, ...rows)));
}

const none = (text) => el("div", { class: "dev-none faint small" }, text);

/** ×0.50 — the median of after ÷ before over the routines judged. */
const ratio = (r) => (r == null ? "—" : `×${r.toFixed(2)}`);
/** +10 pp — the median change of the owed-lines-met share, in percentage points. */
function points(d) {
  if (d == null) return "—";
  const pp = Math.round(d * 100);
  return pp === 0 ? "±0 pp" : `${pp > 0 ? "+" : "−"}${Math.abs(pp)} pp`;
}

function releases(rows) {
  if (!rows.length) return none("no release has reached a routine that held still around it yet");
  return table("chg-releases", [
    th("release"), th("first seen"),
    th("routines", "routines whose behaviour key held still around the release — judged on their own runs", "num"),
    th("verdicts"),
    th("tokens", "median tokens per run, after ÷ before", "num"),
    th("met", "median change of the owed lines met", "num")],
  rows.map((r) => el("tr", { "data-release": r.release },
    el("td", { class: "mono inline" }, r.release),
    el("td", { class: "inline" }, when(r.first_seen, { mode: "rel" })),
    el("td", { class: "num inline", "data-label": "routines", title: (r.routines || []).join(", ") },
      String((r.routines || []).length)),
    el("td", {}, verdictCounts(r.verdicts)),
    el("td", { class: "num inline", "data-label": "tokens" }, ratio(r.tokens_ratio)),
    el("td", { class: "num inline", "data-label": "met" }, points(r.met_rate_delta)))));
}

function fleet(rows) {
  if (!rows.length) return none("no model, effort or rule change measured yet");
  return table("chg-fleet", [th("change"), th("since"),
    th("routines", "each routine that met this change, with its verdict")],
  rows.map((r) => el("tr", { "data-change": `${r.kind}:${r.name}` },
    el("td", { class: "mono inline", title: `${r.from || "—"} → ${r.to || "—"}` }, whatText(r)),
    el("td", { class: "inline" }, when(r.at, { mode: "rel" })),
    el("td", {}, el("span", { class: "v-chips" },
      ...Object.entries(r.routines || {}).map(([slug, verdict]) =>
        verdictChip(verdict, { text: slug, href: `#/changes/${encodeURIComponent(slug)}` })))))));
}

function routines(rows) {
  if (!rows.length) return none("no routine has a measured change yet");
  return table("chg-routines", [th("routine"), th("changes", "", "num"), th("latest"), th("when")],
    rows.map((r) => el("tr", { "data-routine": r.routine },
      el("td", { class: "inline" },
        el("a", { class: "mono", href: `#/changes/${encodeURIComponent(r.routine)}` }, r.routine)),
      el("td", { class: "num inline", "data-label": "changes" }, String(r.changes)),
      el("td", { class: "inline" }, verdictChip(r.latest)),
      el("td", { class: "inline" }, when(r.at, { mode: "rel" })))));
}

export async function render(view) {
  view.classList.add("dev-view");
  const count = el("span", { class: "mono faint" });
  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("div", { class: "kicker dev" }, "development"),
      el("h1", {}, "Changes"),
      el("div", { class: "sub" },
        "Each change judged on its routine's runs before against after — flag-first, nothing reverts on its own. ",
        count))));
  const body = el("div", {}, skeleton(["40%", "100%", "100%", "60%"]));
  view.append(body);

  let alive = true, seq = 0;
  async function load() {
    const mine = ++seq;
    let data;
    try { data = await api("/api/changes"); }
    catch (err) {
      if (alive && mine === seq) body.replaceChildren(emptyState("✕", "changes unavailable", err.message));
      return;
    }
    if (!alive || mine !== seq) return;
    count.textContent = data.measured_runs ? `${data.measured_runs} measured runs` : "";
    if (!data.measured_runs) {
      body.replaceChildren(el("div", { class: "dev-none muted", "data-unmeasured": "" },
        "Nothing measured yet — measurement starts with the runs recorded from this release on."));
      return;
    }
    body.replaceChildren(
      el("h2", { id: "sec-releases" }, "Releases"), releases(data.releases || []),
      el("h2", { id: "sec-fleet" }, "Model & rule changes"), fleet(data.fleet || []),
      el("h2", { id: "sec-routines" }, "Routines"), routines(data.routines || []));
  }

  await load();
  const stop = onRunFinished(() => { load().catch(() => {}); });
  return () => { alive = false; stop(); };
}
