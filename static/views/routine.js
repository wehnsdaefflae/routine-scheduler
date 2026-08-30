// Routine detail: schedule, permissions (user-only toggles), budgets, models, origin, and the
// navigable recipe (main.md + stage modules), then state & runs.

import { api } from "/static/api.js";
import { renderConfigSections } from "/static/views/routine-config.js";
import { setupCheck } from "/static/components/setupcheck.js";
import { mountHealth } from "/static/views/routine-health.js";
import { mountMessages } from "/static/views/routine-messages.js";
import { mountRecipe } from "/static/views/routine-recipe.js";
import { groupSections, routineHero } from "/static/views/routine-overview.js";
import { confirmDialog } from "/static/components/dialog.js";
import { mdInline } from "/static/md.js";
import { chip, el, emptyState, fmtDur, fmtNum, fmtTokens, skeleton, toast, when } from "/static/util.js";

// The config sections (rendered flat by routine-config.js + the recipe/state blocks below)
// are regrouped into these labeled, collapsible groups — an operator scans the group they
// need instead of a single wall. Order = most-touched first; every heading each module emits
// is claimed here, and groupSections keeps any stray in a trailing "More" group.
const SECTION_GROUPS = [
  { title: "Schedule & triggers", hint: "when and how it fires",
    headings: ["Schedule", "Triggers", "Schedule once"] },
  // "Start from a template" leads this group: it is the one control that writes the other
  // two wholesale, so an operator reading "what may it do?" meets the starting point before
  // the per-routine edits. Unclaimed, it fell into the trailing "More" fold and was unreachable.
  { title: "Permissions & practices", hint: "its starting point, what it may do, and how it works",
    headings: ["Start from a template", "Recommended setup", "Permissions & capabilities",
               "General rules", "Effective surface"] },
  // D103: the two secret scopes read together — what this routine OWNS, and which shared
  // names it may be handed. Before this group they fell into the trailing "More" fold.
  { title: "Secrets & access", hint: "its own credentials · shared-store exposure · settled denials",
    headings: ["Own secrets", "Secret exposure", "Declined access"] },
  // "Goal" leads this group, ahead of the budgets: F334/D98's whole claim is that budgets are a
  // runaway BACKSTOP and the stopping conditions are what decides when a job is finished, so
  // the group that holds the ceilings has to say the meaning-level bound first.
  { title: "Goal & limits", hint: "what DONE means · per-run ceilings · retention · filesystem reach",
    headings: ["Goal", "Budgets", "Retention", "Filesystem roots"] },
  { title: "Models & resources", hint: "models · connections · machines",
    headings: ["Models", "Connections", "Machines"] },
  { title: "Recipe & memory", hint: "the workflow files, their health, and run state",
    headings: ["Recipe health", "Recipe", "State & memory"] },
  { title: "Identity & origin", hint: "name · description · tags · provenance",
    headings: ["Name", "Description", "Tags", "Origin"] },
];

export async function render(view, slug, query = {}) {
  view.append(skeleton(["35%", "100%", "70%"]));
  let d, st;
  try { [d, st] = await Promise.all([api(`/api/routines/${slug}`), api("/api/status").catch(() => ({}))]); }
  catch (err) { view.replaceChildren(emptyState("✕", `Couldn't load ${slug}`, err.message)); return; }
  view.replaceChildren();
  const llmReady = st.llm_ready !== false;

  const runChip = (x) => (x.active_state ? chip(x.active_state, x.active_state)
    : x.enabled ? chip("idle", "idle") : chip("disabled", "disabled"));
  const chipHost = el("span", {}, runChip(d));
  const titleH1 = el("h1", {}, d.name || slug);
  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("div", { class: "kicker" }, "routine"),
      titleH1),
    el("div", { class: "row" }, chipHost,
      d.active_run
        ? el("a", { class: "btn primary", href: `#/run/${d.active_run}` }, "◉ watch live")
        : el("button", { class: "btn primary", disabled: !llmReady,
            title: llmReady ? "" : "connect an LLM endpoint in Settings first", onclick: runNow }, "▶ run now"),
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
  const setupHost = el("div", {});
  view.append(setupHost);
  d.surface = await setupCheck(setupHost, slug);

  async function runNow(e) {
    e.target.disabled = true;
    try { const r = await api(`/api/routines/${slug}/run`, { method: "POST" });
      location.hash = `#/run/${r.run_id}`; }
    catch (err) { toast(err.message, 4000, { error: true }); e.target.disabled = false; }
  }
  async function archive() {
    if (!(await confirmDialog(`Archive "${slug}"? It leaves the scheduler (dir moves to .archive).`, { confirmLabel: "archive" }))) return;
    try { await api(`/api/routines/${slug}/archive`, { method: "POST" }); location.hash = "#/routines"; }
    catch (err) { toast(err.message, 4000, { error: true }); }
  }

  // -- decisions (actionable — kept in the overview zone, never folded into a config group) --
  if (d.questions?.length) {
    const openCount = d.questions.filter((q) => !q.answered).length;
    view.append(el("h2", {}, `Decisions · ${openCount}`),
      el("div", { class: "panel warn" }, d.questions.map((q) =>
        el("div", { class: "row spread", style: "padding:5px 0" },
          el("span", { class: "prose" }, q.answered ? "✓ " : "❓ ", mdInline(q.question)),
          q.answered
            ? chip("answered — queued for next run", "waiting_user")
            : el("a", { class: "btn small primary",
                        href: `#/questions?routine=${encodeURIComponent(slug)}` },
                 "answer")))));
  }

  // -- runs (recent activity — kept in the overview zone) --------------------------
  view.append(el("h2", {}, "Runs"));
  const runsBox = el("div", { class: "runs-box" });
  view.append(runsBox);
  renderRuns(d);

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

  // -- config + recipe: rendered flat into a DETACHED host, then regrouped by groupSections
  // into labeled, collapsible groups. Every section body is untouched; the async panels
  // (permissions/rules/connections/machines) fill node refs that grouping only relocates. --
  const cfgHost = el("div", {});
  const { refreshHead } = renderConfigSections(cfgHost, d, { slug, titleH1, chipHost, runChip });

  // recipe health: runs bucketed by the recipe version that produced them (engine-stamped
  // commit; the durable usage stream survives retention). Flags a regressing recipe change —
  // flag-first, the roll-back is the user's click.
  const healthBox = el("div", { class: "panel" }, skeleton(["60%", "90%"]));
  cfgHost.append(el("h2", {}, "Recipe health"), healthBox);

  // recipe: the routine's OWN workflow files (main.md + stage modules) — a
  // navigable tree; edits go through the generic /file endpoint. A run never edits its own
  // recipe/config, so this editor is the human's lever on the recipe.
  cfgHost.append(el("h2", {}, "Recipe"));
  const navCol = el("div", { class: "recipe-navcol" }, skeleton(["80%", "60%", "70%"]));
  const editorCol = el("div", { class: "recipe-editorcol" },
    el("div", { class: "muted small" }, "pick a file on the left to view or edit it"));
  cfgHost.append(el("div", { class: "panel" },
    el("div", { class: "muted small", style: "margin-bottom:10px" },
      "the routine's OWN workflow — ", el("strong", {}, "main.md"), " routes through the ",
      el("strong", {}, "stage"), " modules (in run-flow order). The general rules it holds "
      + "live in the library, not here. Edit freely; the routine-improver may also refine these."),
    el("div", { class: "recipe-wrap" }, navCol, editorCol)));
  const recipe = mountRecipe(navCol, editorCol, slug, query.file || "");
  const health = mountHealth(healthBox, slug, { onRecipeChanged: recipe.refreshTree });

  // state + ledger
  const stateFiles = (d.files?.state) || [];
  cfgHost.append(el("h2", {}, "State & memory"),
    el("div", { class: "panel" },
      el("div", { class: "muted small" },
        stateFiles.length ? `state/ · ${stateFiles.join("  ·  ")}` : "no state files yet"),
      el("details", { class: "mt" }, el("summary", { style: "cursor:pointer" }, "LEDGER tail"),
        el("pre", { class: "doc mt" }, d.ledger_tail || "(empty)"))));

  view.append(groupSections(cfgHost, SECTION_GROUPS));

  // The page used to be a static snapshot — a run finishing while you look at it left a
  // stale hub. Its own run lifecycle events refresh the header chip, health, and runs.
  const onBus = async (e) => {
    const ev = e.detail || {};
    if (!["run_started", "run_finished"].includes(ev.event)) return;
    if (!String(ev.run_id || "").startsWith(`${slug}:`)) return;
    refreshHead();
    messagesPane?.reload();   // a run drains the inbox at boot and files reports as it works
    if (ev.event === "run_finished") {
      health.reload();
      try { renderRuns(await api(`/api/routines/${slug}`)); } catch { /* keep the old table */ }
    }
  };
  window.addEventListener("rsched-bus", onBus);
  return () => window.removeEventListener("rsched-bus", onBus);

  // The Runs table is capped (user order 2026-08-15, F345): with keep_runs at 30+ the full
  // history made this element the tallest thing on the page, pushing every section below
  // the fold. The newest rows answer "is it healthy right now"; the full history is one
  // explicit click away (the expanded state survives the live re-render on run_finished
  // because it rides on runsBox itself, not on this closure).
  function renderRuns(d) {
  const RUNS_PREVIEW = 10;   // inside the function: it hoists, a const out here would not
  runsBox.replaceChildren();
  const view = runsBox;
  const all = d.runs || [];
  const expanded = runsBox.dataset.expanded === "1";
  const shown = expanded ? all : all.slice(0, RUNS_PREVIEW);
  const rows = shown.map((r) => el("tr", {},
    el("td", {}, el("a", { href: `#/run/${r.run_id}` }, when(r.ts))),
    el("td", {}, chip(r.state, r.state)),
    el("td", { class: "num" }, String(r.turn ?? "")),
    el("td", { class: "num muted" }, r.elapsed_s != null ? fmtDur(r.elapsed_s) : "—"),
    el("td", { class: "muted" }, fmtTokens(r.usage)),
    el("td", { class: "muted prose", style: "max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" },
      r.summary || "")));
  view.append(el("div", { class: "panel", style: "padding:0" },
    el("div", { class: "tablewrap" },
      el("table", { class: "list" },
        el("thead", {}, el("tr", {}, ["when", "state", "turns", "duration", "tokens", "summary"].map((h) => el("th", {}, h)))),
        el("tbody", {}, rows.length ? rows
          : el("tr", {}, el("td", { class: "muted", colspan: 6 }, "no runs yet — fire one with ▶ run now")))))));
  if (all.length > RUNS_PREVIEW) {
    view.append(el("div", { class: "row", style: "justify-content:center;padding:6px 0" },
      el("button", { class: "btn small", onclick: () => {
        runsBox.dataset.expanded = expanded ? "" : "1";
        renderRuns(d);
      } }, expanded ? "show fewer" : `show all ${all.length} runs`)));
  }
}
}
