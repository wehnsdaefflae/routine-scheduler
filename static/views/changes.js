// Changes — the DEVELOPMENT view of the fleet (#/changes, rail group Develop). Did a change
// help? Every change is found from the runs' own fingerprints and judged on the runs before
// against the runs after it (readmodels/change_effects.py, served by GET /api/changes). The
// history reaches back months — runs from before fingerprints were REBUILT from git — so every
// list here shows its newest rows and folds the rest behind one "show all":
//
//   Weeks                  — the fleet week by week as small multiples (components/sparkline.js):
//                            runs, failed share, lines met, tokens against each routine's usual,
//                            interventions and challenges per run; releases as ticks, weeks
//                            mostly rebuilt shaded;
//   Releases               — each engine release judged on the POOLED runs of the routines that
//                            held still around it: one verdict, how many routines, the token ratio
//                            and the met-rate change; how many other releases shared its windows
//                            and how many routines it had to leave out, only when there are any;
//   Model & rule changes   — one row per model / effort / rule change: the verdict pooled where it
//                            was the ONLY change at that run ("alone in A of R"), the batch's
//                            verdict beside it where it came with others, and each routine's own;
//   Routines               — every routine with a measured change and the way into its own
//                            development view (#/changes/<slug>).
//
// "≈" marks a reading drawn on rebuilt runs; a routine that is gone (archived) still counts and
// is named, unlinked. Production and development are kept apart (operator, 2026-10-08), and the
// console is text heavy already: chips, glyphs and mono numbers here, the explanations in
// tooltips. Re-read on a finished run only (components/run-finished.js), and only the newest
// read paints; a list the reader unfolded stays unfolded across the re-read.

import { api } from "/static/api.js";
import { verdictChip, whatText } from "/static/components/change-marks.js";
import { foldEntries, rebuiltMark } from "/static/components/change-folds.js";
import { onRunFinished } from "/static/components/run-finished.js";
import { fmt, sparkStrip } from "/static/components/sparkline.js";
import { el, emptyState, skeleton, when } from "/static/util.js";

const th = (label, title = "", cls = "") => el("th", { title: title || null, class: cls || null }, label);

function table(cls, heads, rows) {
  return el("div", { class: "tablewrap" },
    el("table", { class: `list stack ${cls}` },
      el("thead", {}, el("tr", {}, ...heads)), el("tbody", {}, ...rows)));
}

const none = (text) => el("div", { class: "dev-none faint small" }, text);
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const QUIET = new Set(["too few runs", ""]);

/** ×0.50 — after ÷ before, each routine's runs read against its own median. */
const ratio = (r) => (r == null ? "—" : `×${r.toFixed(2)}`);
/** +10 pp — the change of the owed-lines-met share, in percentage points. */
function points(d) {
  if (d == null) return "—";
  const pp = Math.round(d * 100);
  return pp === 0 ? "±0 pp" : `${pp > 0 ? "+" : "−"}${Math.abs(pp)} pp`;
}

/** A routine's name as a chip: linked to its development view while it exists, muted and
 *  unlinked once archived (its runs still count). An archive is named `<slug>-<stamp>`. */
function routineChip(name, verdict, archived) {
  if (!archived.has(name)) {
    return verdictChip(verdict, { text: name, href: `#/changes/${encodeURIComponent(name)}` });
  }
  const slug = name.replace(/-\d{8}-\d{6}$/, "");
  const chip = verdictChip(verdict, { text: `${slug} (archived)` });
  chip.classList.add("v-archived");
  chip.dataset.archived = name;
  chip.title = `${name} — archived; its runs still count. ${chip.title}`;
  return chip;
}

const FLEET_SERIES = [
  { key: "runs", label: "runs", title: "runs per week", pick: (w) => w.runs, format: fmt.count, lo: 0, minSpan: 1 },
  { key: "failed", label: "failed", title: "share of runs failed or aborted",
    pick: (w) => w.signals?.failed, format: fmt.share, lo: 0, minSpan: 0.1 },
  { key: "met_rate", label: "lines met", title: "share of owed Done-when lines met",
    pick: (w) => w.signals?.met_rate, format: fmt.share, hi: 1, minSpan: 0.2 },
  { key: "tokens", label: "tokens ×", log: true, lo: 0.25, hi: 4, ref: 1,
    title: "tokens per run against each routine's own median — ×1.00 is usual (log scale, ×0.25–×4)",
    pick: (w) => w.signals?.tokens, format: fmt.ratio },
  { key: "interventions", label: "interventions", title: "your interventions per run",
    pick: (w) => w.signals?.interventions, format: fmt.per, lo: 0, minSpan: 0.5 },
  { key: "challenged", label: "challenged", title: "claims the verifier challenged per run",
    pick: (w) => w.signals?.challenged, format: fmt.per, lo: 0, minSpan: 0.5 },
];

function releaseRow(r) {
  const n = (r.routines || []).length;
  const notes = [
    r.smeared ? el("span", { title: "the median number of OTHER releases inside the judged windows — the verdict covers that stretch, not this release alone" },
      `+${plural(r.smeared, "release")} in window`) : null,
    r.confounded ? el("span", { title: `${plural(r.confounded, "routine")} changed at the same run, left out — their recipe, config or rules changed with the release` },
      `${r.confounded} left out`) : null,
  ].filter(Boolean);
  return el("tr", { "data-release": r.release, class: r.verdict === "too few runs" ? "quiet" : null },
    el("td", { class: "mono inline" }, r.release),
    el("td", { class: "inline" }, when(r.first_seen, { mode: "rel" })),
    el("td", { class: "num inline", "data-label": "routines",
               title: `${(r.routines || []).join(", ") || "none held still"} — pooled ${r.runs_before} runs before → ${r.runs_after} after` },
      String(n)),
    el("td", { class: "inline" }, el("span", { class: "v-line" },
      verdictChip(r.verdict), r.reconstructed ? rebuiltMark() : null)),
    el("td", { class: "num inline", "data-label": "tokens" }, ratio(r.tokens_ratio)),
    el("td", { class: "num inline", "data-label": "met" }, points(r.met_rate_delta)),
    el("td", { class: "chg-notes faint small" }, ...notes.flatMap((x, i) => (i ? [" · ", x] : [x]))));
}

function releases(rows, folds) {
  if (!rows.length) return none("no release has reached a routine that held still around it yet");
  return table("chg-releases", [
    th("release"), th("first seen"),
    th("routines", "routines whose behaviour key held still around the release — their runs pooled", "num"),
    th("verdict", "judged on the pooled runs before → after the release"),
    th("tokens", "tokens per run after ÷ before, each routine against its own median", "num"),
    th("met", "change of the owed lines met", "num"), th("")],
  foldEntries(rows.map((r) => [releaseRow(r)]), {
    keep: 12, key: "releases", state: folds, closed: `show all ${rows.length} releases` }));
}

/** The routines a change reached, each with its own verdict; the quiet ones ("too few runs")
 *  fold into one count once there are more than three of them (`key` in `folds` while open). */
function reachedChips(routines, archived, folds, key) {
  const entries = Object.entries(routines || {});
  const loud = entries.filter(([, v]) => !QUIET.has(v));
  const quiet = entries.filter(([, v]) => QUIET.has(v));
  const chips = loud.map(([name, v]) => routineChip(name, v, archived));
  if (quiet.length <= 3) {
    chips.push(...quiet.map(([name, v]) => routineChip(name, v, archived)));
    return el("span", { class: "v-chips" }, ...chips);
  }
  const hiddenBox = el("span", { class: "v-chips", hidden: !folds.has(key) },
    ...quiet.map(([name, v]) => routineChip(name, v, archived)));
  const more = el("button", { type: "button", class: "chip bare disabled v-more",
                              "aria-expanded": String(folds.has(key)),
                              title: `${quiet.length} routines met it too soon to judge on their own runs — show them` },
    `∅ ${quiet.length} too few runs`);
  more.addEventListener("click", () => {
    hiddenBox.hidden = !hiddenBox.hidden;
    if (hiddenBox.hidden) folds.delete(key); else folds.add(key);
    more.setAttribute("aria-expanded", String(!hiddenBox.hidden));
  });
  return el("span", { class: "v-chips" }, ...chips, more, hiddenBox);
}

function fleetRow(r, archived, folds) {
  const reach = Object.keys(r.routines || {}).length;
  const t = r.together;
  return el("tr", { "data-change": `${r.kind}:${r.name}`, "data-to": r.to || "",
                    class: r.verdict === "too few runs" && !t ? "quiet" : null },
    el("td", { class: "mono inline", title: `${r.from || "—"} → ${r.to || "—"}` }, whatText(r),
      r.kind === "rule" && r.from && r.to   // a rule revised twice is two rows: which text it became
        ? el("span", { class: "faint" }, ` →${r.to.slice(0, 7)}`) : null),
    el("td", { class: "inline" }, when(r.at, { mode: "rel" })),
    el("td", {},
      el("div", { class: "v-line" }, verdictChip(r.verdict), r.reconstructed ? rebuiltMark() : null,
        el("span", { class: "faint small mono", "data-alone": r.alone,
                     title: `the verdict is pooled over the ${plural(r.alone, "routine")} where this was the ONLY change at that run — ${r.runs_before} runs before → ${r.runs_after} after` },
          `alone in ${r.alone} of ${reach}`)),
      t ? el("div", { class: "v-line v-together faint small", "data-together": t.verdict,
                      title: `pooled over every routine it reached, where it changed together with other things — what the batch did: ${t.runs_before} runs before → ${t.runs_after} after` },
        "with other changes:", verdictChip(t.verdict), t.reconstructed ? rebuiltMark() : null,
        el("span", { class: "mono" }, `(${plural(reach, "routine")})`)) : null),
    el("td", {}, reachedChips(r.routines, archived, folds, `quiet:${r.kind}:${r.name}:${r.from}:${r.to}`)));
}

function fleet(rows, archived, folds) {
  if (!rows.length) return none("no model, effort or rule change measured yet");
  return table("chg-fleet", [th("change"), th("since"),
    th("verdict", "pooled where this was the only change; the batch's verdict below it"),
    th("routines", "each routine that met this change, with its own verdict")],
  foldEntries(rows.map((r) => [fleetRow(r, archived, folds)]), {
    keep: 10, key: "fleet", state: folds, closed: `show all ${rows.length} changes` }));
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

  const folds = new Set();        // the lists a reader unfolded survive a live re-read
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
    const archived = new Set(data.archived || []);
    const weeks = data.timeline || [];
    body.replaceChildren(...[
      weeks.length ? el("h2", { id: "sec-weeks" }, "Weeks") : null,
      sparkStrip(weeks, FLEET_SERIES),
      el("h2", { id: "sec-releases" }, "Releases"), releases(data.releases || [], folds),
      el("h2", { id: "sec-fleet" }, "Model & rule changes"), fleet(data.fleet || [], archived, folds),
      el("h2", { id: "sec-routines" }, "Routines"), routines(data.routines || []),
    ].filter(Boolean));
  }

  await load();
  const stop = onRunFinished(() => { load().catch(() => {}); });
  return () => { alive = false; stop(); };
}
