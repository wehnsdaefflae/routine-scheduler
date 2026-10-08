// Routine detail — a PRODUCTION page: the overview (status, lane, last run, spend, decisions),
// the runs and the routine's messages — then its SETTINGS, one form led by its pattern and saved
// by one accept (views/routine-config.js), with the recipe and its state folded in beside them.
// How the routine's changes have done (verdicts, model fit, recipe health) is DEVELOPMENT
// information and lives at #/changes/<slug>; this page carries one line pointing there, under its
// name (components/dev-line.js).

import { api } from "/static/api.js";
import { renderSettings } from "/static/views/routine-config.js";
import { setupCheck } from "/static/components/setupcheck.js";
import { loadSettings } from "/static/components/settings-form.js";
import { landOn } from "/static/components/settings-field.js";
import { mountMessages } from "/static/views/routine-messages.js";
import { mountTasks } from "/static/components/tasks-panel.js";
import { routineHero } from "/static/views/routine-overview.js";
import { trialChip } from "/static/components/trialchip.js";
import { confirmDialog } from "/static/components/dialog.js";
import { devLine } from "/static/components/dev-line.js";
import { summaryLine } from "/static/md.js";
import { chip, el, emptyState, fmtDur, fmtTokens, skeleton, toast, toastError, when } from "/static/util.js";

export async function render(view, slug, query = {}) {
  view.append(skeleton(["35%", "100%", "70%"]));
  let d, st, settings;
  try {
    // the settings are read beside the detail, so the page's sections all exist by the time it
    // returns — the side table-of-contents indexes the headings the view has rendered by then
    [d, st, settings] = await Promise.all([api(`/api/routines/${slug}`),
      api("/api/status").catch(() => ({})),
      loadSettings(slug).catch((err) => ({ error: err.message }))]);
  } catch (err) { view.replaceChildren(emptyState("✕", `Couldn't load ${slug}`, err.message)); return; }
  view.replaceChildren();
  const llmReady = st.llm_ready !== false;

  // Three reasons a routine is not running, and they are not interchangeable: it is between
  // runs, it reached its FINISH LINE and is done, or you switched it off. Beside it, a MODEL
  // TRIAL the routine is on (components/trialchip.js) — both repainted together on every head
  // refresh, so a run that finishes moves the trial's count with the run chip.
  const stateChip = (x) => (x.active_state ? chip(x.active_state, x.active_state)
    : x.retired ? chip("finished", "finished")
    : x.enabled ? chip("idle", "idle") : chip("disabled", "disabled"));
  const runChip = (x) => el("span", { class: "row head-chips" }, stateChip(x), trialChip(x.trial, slug));
  const chipHost = el("span", {}, runChip(d));
  const titleH1 = el("h1", {}, d.name || slug);
  // Run now takes an optional BRIEF: one line the run started by hand answers for, instead of
  // its recipe's Done when (engine/brief.py). Empty is an ordinary run of the recipe.
  const briefIn = el("input", { type: "text", class: "run-brief tight", maxlength: "300",
    placeholder: "one job for this run (optional)", "data-run-brief": "", "data-nopersist": true,
    title: "a one-line brief for the run you start now — it answers for this instead of its "
      + "recipe's Done when; leave it empty for an ordinary run",
    onkeydown: (e) => { if (e.key === "Enter" && !runBtn.disabled) { e.preventDefault(); runNow(); } } });
  const runBtn = el("button", { class: "btn primary", disabled: !llmReady, "data-run-now": "",
    title: llmReady ? "" : "connect an LLM endpoint in Settings first", onclick: () => runNow() },
    "▶ run now");
  // the one line about development: the way to this routine's changes, model fit and recipe
  // health, and the summons tone when its newest change regressed
  const dev = devLine(slug);
  view.append(el("div", { class: "page-head" },
    el("div", {},
      titleH1, dev.node),
    el("div", { class: "row" }, chipHost,
      ...(d.active_run
        ? [el("a", { class: "btn primary", href: `#/run/${d.active_run}` }, "◉ watch live")]
        : [briefIn, runBtn]),
      el("button", { class: "btn danger", onclick: archive }, "archive"))));
  if (d.problems?.length) {
    view.append(el("div", { class: "panel err", style: "margin-top:14px" },
      d.problems.map((p) => el("div", { style: "color:var(--err)" }, `⚠ ${p}`))));
  }

  // --- overview hero: the informative first screen (status · last run · spend · decisions) ---
  view.append(routineHero(d, slug));

  // --- setup check: what the panels below ADD UP TO. Above the fold and above the hero's
  // detail, because a routine that cannot do its job should say so before it says anything
  // else. Rendered async and silent when there is nothing outstanding.
  // ONE fetch feeds both readers of the surface: the strip here, and the ability cards
  // below, which hang each resolved need under the ability that owns it.
  // Each strip row also offers the act that closes it, addressing a `sec-*` anchor that
  // renderSettings puts on the page further down — so the strip is mounted first and aims at
  // sections that appear after it. The offers are pressed by a reader, long after this render
  // finishes; a strip mounted into a page whose settings never render would find nothing to
  // land on and say so on the button instead. The act is a change to the draft below, while the
  // strip describes what the routine HOLDS — so it is repainted when the change is accepted.
  // `repaintSetup` hands that repaint to the one module that knows an accept landed.
  const setupHost = el("div", {});
  view.append(setupHost);
  d.surface = await setupCheck(setupHost, slug);

  async function runNow() {
    runBtn.disabled = true;
    const brief = briefIn.value.trim();
    try {
      const r = await api(`/api/routines/${slug}/run`,
        { method: "POST", ...(brief ? { body: { brief } } : {}) });
      location.hash = `#/run/${r.run_id}`;
    } catch (err) { toastError(err); runBtn.disabled = false; }
  }
  async function archive() {
    if (!(await confirmDialog(`Archive "${slug}"? It leaves the scheduler (dir moves to .archive).`, { confirmLabel: "archive" }))) return;
    try {
      const r = await api(`/api/routines/${slug}/archive`, { method: "POST" });
      // Archiving cleans up what it owns and CANNOT touch what the routine published
      // elsewhere — a steward card outlived its routine three times in two weeks because
      // this moment passed in silence (R1658, bina). Say what is still out there and who
      // can remove it, while the person who just archived it is still looking. ONE toast:
      // there is one #toast, and a toast per surface overwrote all but the last.
      const residue = (r.external_residue || []).map((item) =>
        `Still on the ${item.surface}: ${item.locator}. `
        + `Archiving cannot remove it — ask ${item.owner} to retire it.`);
      if (residue.length) toast(residue.join(" "), 12000, { error: true });
      location.hash = "#/routines";
    } catch (err) { toastError(err); }
  }

  // -- decisions (actionable — kept in the overview zone, never folded into a config group) --
  if (d.questions?.length) {
    const openCount = d.questions.filter((q) => !q.answered).length;
    view.append(el("h2", {}, `Decisions · ${openCount}`),
      el("div", { class: "panel warn" }, d.questions.map((q) =>
        el("div", { class: "row spread", style: "padding:5px 0" },
          // A row with an "answer" button beside it, so this is a PREVIEW: the first line
          // only, the way a run summary previews on the dashboard. A question long enough to
          // carry a table is read where it is answered; a block body cannot sit in a row.
          el("span", { class: "prose", title: q.question },
            q.answered ? "✓ " : "❓ ",
            summaryLine(q.question, "(no question)")),
          q.answered
            // Answered and waiting for the routine's next run: that waits on a MACHINE, so it
            // is not the summons colour. Coral is only ever "this needs a person".
            ? chip("answered — queued for next run", "idle")
            : el("a", { class: "btn small primary",
                        href: `#/questions?routine=${encodeURIComponent(slug)}` },
                 "answer")))));
  }

  // -- tasks: a routine whose task layer is on keeps its standing work as TASKS
  // (components/tasks-panel.js) — what each one is, what the last run did for it, and the
  // operator's pause / resume / done. Overview zone: it is state, not a setting.
  let tasksPane = null;
  if (d.tasks_enabled) {
    view.append(el("h2", { id: "sec-tasks" }, "Tasks"));
    const tasksHost = el("div", {});
    view.append(tasksHost);
    tasksPane = mountTasks(tasksHost, slug, { active: Boolean(d.active_run) });
  }

  // -- runs (recent activity — kept in the overview zone) --------------------------
  view.append(el("h2", {}, "Runs"));
  const runsBox = el("div", { class: "runs-box" });
  view.append(runsBox);
  runsTable(runsBox, d);

  // -- messages (D74): the four folders — inbox (write/edit/withdraw until a run drains
  // it; this is where a "note for the next run" lives), outbox (retractable hand-offs),
  // read + received (history).
  let messagesPane = null;
  {
    view.append(el("h2", {}, "Messages"));
    const msgHost = el("div", {});
    view.append(msgHost);
    messagesPane = mountMessages(msgHost, slug);
  }

  // -- settings: one form over every setting, led by the routine's pattern, saved by one
  // accept. The recipe and its state fold in beside them.
  if (settings.error) {
    view.append(el("h2", { id: "sec-settings" }, "Settings"),
      el("div", { class: "panel err" }, `the settings could not be read: ${settings.error}`));
    return () => { dev.dispose(); };
  }
  const cfg = renderSettings(view, d, settings, {
    slug, titleH1, chipHost, runChip, recipeFile: query.file || "",
    // The strip is a READER of the surface; the settings are what move it. So the settings
    // side re-reads once per accept and hands the answer here, where the strip lives.
    repaintSetup: (surface) => setupCheck(setupHost, slug, surface),
  });
  // `?section=<id>` lands the reader on one section, opening the folds on the way: the
  // Decisions page sends a finish line the calendar reached to its Goal settings.
  if (query.section) {
    requestAnimationFrame(() => landOn(view, document.getElementById(`sec-${query.section}`)));
  }

  // The page used to be a static snapshot — a run finishing while you look at it left a
  // stale hub. Its own run lifecycle events refresh the header chip, the development line, and
  // the runs.
  const onBus = async (e) => {
    const ev = e.detail || {};
    if (!["run_started", "run_finished"].includes(ev.event)) return;
    if (!String(ev.run_id || "").startsWith(`${slug}:`)) return;
    // ONE read of the detail serves both of its readers here — the header chip and next fire,
    // and once a run has finished, the runs table. It was read twice, a moment apart.
    const head = cfg.refreshHead();
    messagesPane?.reload();   // a run drains the inbox at boot and files reports as it works
    tasksPane?.reload({ running: ev.event === "run_started" });   // a run decides what it owes
    if (ev.event === "run_finished") {
      dev.reload();   // a finished run is what lands a measured change
      // a run moves the surface too: it can reach the finish line, write the phase file the
      // `state:phase` row asks for, or author the util a held capability names — and it reports
      // on the finish line, which the Goal group reads
      cfg.refreshSurface();
      cfg.onRunFinished();
      const nd = await head;
      if (nd) runsTable(runsBox, nd);   // a failed read keeps the old table
    }
  };
  window.addEventListener("rsched-bus", onBus);
  return () => { window.removeEventListener("rsched-bus", onBus); cfg.dispose(); dev.dispose(); };
}

// The Runs table is capped (user order 2026-08-15, F345): with keep_runs at 30+ the full
// history made this element the tallest thing on the page, pushing every section below the
// fold. The newest rows answer "is it healthy right now"; the full history is one explicit
// click away (the expanded state survives the live re-render on run_finished because it rides
// on runsBox itself, not on a closure).
const RUNS_PREVIEW = 10;

function runsTable(runsBox, d) {
  runsBox.replaceChildren();
  const all = d.runs || [];
  const expanded = runsBox.dataset.expanded === "1";
  const shown = expanded ? all : all.slice(0, RUNS_PREVIEW);
  // `inline` marks the cells that share ONE line when the table stacks on a phone (base.css):
  // when · state · turns · duration · tokens, and the summary below them with the width it
  // needs. Unstacked they are ordinary columns and the class does nothing.
  const rows = shown.map((r) => el("tr", {},
    el("td", { class: "inline" }, el("a", { href: `#/run/${r.run_id}` }, when(r.ts))),
    el("td", { class: "inline" }, chip(r.state, r.state)),
    el("td", { class: "num inline", "data-label": "turns" }, r.turn == null ? null : String(r.turn)),
    el("td", { class: "num muted inline" }, r.elapsed_s != null ? fmtDur(r.elapsed_s) : "—"),
    el("td", { class: "muted inline" }, fmtTokens(r.usage)),
    // The summary is MODEL PROSE, and four surfaces show this one field — so ONE helper
    // reduces it (md.summaryLine): first non-empty line, its heading marker dropped, rendered.
    // Spelled out per surface it drifted, and `**One application went out…**` reached this
    // cell with its asterisks.
    el("td", { class: "muted prose", style: "max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" },
      summaryLine(r.summary))));
  runsBox.append(el("div", { class: "panel", style: "padding:0" },
    el("div", { class: "tablewrap" },
      el("table", { class: "list stack" },
        el("thead", {}, el("tr", {}, ["when", "state", "turns", "duration", "tokens", "summary"].map((h) => el("th", {}, h)))),
        el("tbody", {}, rows.length ? rows
          : el("tr", {}, el("td", { class: "muted", colspan: 6 }, "no runs yet — fire one with ▶ run now")))))));
  if (all.length > RUNS_PREVIEW) {
    runsBox.append(el("div", { class: "row", style: "justify-content:center;padding:6px 0" },
      el("button", { class: "btn small", onclick: () => {
        runsBox.dataset.expanded = expanded ? "" : "1";
        runsTable(runsBox, d);
      } }, expanded ? "show fewer" : `show all ${all.length} runs`)));
  }
}
