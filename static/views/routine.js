// Routine detail: schedule, permissions (user-only toggles), budgets, models, origin, and the
// navigable recipe (main.md + stage modules + trait modules), then state & runs.

import { api } from "/static/api.js";
import { renderConfigSections } from "/static/views/routine-config.js";
import { mountHealth } from "/static/views/routine-health.js";
import { mountRecipe } from "/static/views/routine-recipe.js";
import { confirmDialog } from "/static/components/dialog.js";
import { mdInline } from "/static/md.js";
import { chip, el, emptyState, fmtDur, fmtNum, fmtTokens, skeleton, toast, when } from "/static/util.js";

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
      // the clarification template is wizard configuration: no run, no archive — the
      // server 403s both anyway; hiding the buttons says so up front
      ...(d.protected
        ? [chip("protected template", "disabled")]
        : [d.active_run
            ? el("a", { class: "btn primary", href: `#/run/${d.active_run}` }, "◉ watch live")
            : el("button", { class: "btn primary", disabled: !llmReady,
                title: llmReady ? "" : "connect an LLM endpoint in Settings first", onclick: runNow }, "▶ run now"),
          el("button", { class: "btn danger", onclick: archive }, "archive")]))));
  if (d.problems?.length) {
    view.append(el("div", { class: "panel err", style: "margin-top:14px" },
      d.problems.map((p) => el("div", { style: "color:var(--err)" }, `⚠ ${p}`))));
  }

  const { refreshHead } = renderConfigSections(view, d,
    { slug, titleH1, chipHost, runChip });


  async function runNow(e) {
    e.target.disabled = true;
    try { const r = await api(`/api/routines/${slug}/run`, { method: "POST" });
      location.hash = `#/run/${r.run_id}`; }
    catch (err) { toast(err.message, 4000, { error: true }); e.target.disabled = false; }
  }
  async function archive() {
    if (!(await confirmDialog(`Archive "${slug}"? It leaves the scheduler (dir moves to .archive).`, { confirmLabel: "archive" }))) return;
    try { await api(`/api/routines/${slug}/archive`, { method: "POST" }); location.hash = "#/"; }
    catch (err) { toast(err.message, 4000, { error: true }); }
  }

  // -- recipe health: runs bucketed by the recipe version that produced them --------
  // (engine-stamped recipe commit; the durable usage stream survives run retention).
  // A deterministic heuristic flags the newest recipe change when the runs after it are
  // clearly worse than the runs before — flag-first, the roll-back is YOUR click.
  const healthBox = el("div", { class: "panel" }, skeleton(["60%", "90%"]));
  view.append(el("h2", {}, "Recipe health"), healthBox);
  // (filled by mountHealth below, after the recipe editor exists - its roll-back
  // re-syncs the recipe tree)

  // -- recipe: the routine's OWN workflow files (main.md + stage modules + practice traits) -----
  // A navigable tree that mirrors the markdown files; edits go through the generic /file endpoint.
  // A run never edits its own recipe or config — the routine-improver refines recipes, the user
  // owns config (above) — so this editor is the human's lever on the recipe.
  view.append(el("h2", {}, "Recipe"));
  const navCol = el("div", { class: "recipe-navcol" }, skeleton(["80%", "60%", "70%"]));
  const editorCol = el("div", { class: "recipe-editorcol" },
    el("div", { class: "muted small" }, "pick a file on the left to view or edit it"));
  view.append(el("div", { class: "panel" },
    el("div", { class: "muted small", style: "margin-bottom:10px" },
      "the routine's OWN workflow — ", el("strong", {}, "main.md"), " routes through the ",
      el("strong", {}, "stage"), " modules (in run-flow order); ", el("strong", {}, "traits"),
      " are its adapted practices. Edit freely; the routine-improver may also refine these."),
    el("div", { class: "recipe-wrap" }, navCol, editorCol)));
  const recipe = mountRecipe(navCol, editorCol, slug, query.file || "");
  const health = mountHealth(healthBox, slug, { onRecipeChanged: recipe.refreshTree });

  // -- questions ------------------------------------------------------------------
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

  // -- state + ledger -------------------------------------------------------------
  const stateFiles = (d.files?.state) || [];
  view.append(el("h2", {}, "State & memory"),
    el("div", { class: "panel" },
      el("div", { class: "muted small" },
        stateFiles.length ? `state/ · ${stateFiles.join("  ·  ")}` : "no state files yet"),
      el("details", { class: "mt" }, el("summary", { style: "cursor:pointer" }, "LEDGER tail"),
        el("pre", { class: "doc mt" }, d.ledger_tail || "(empty)"))));

  // -- runs -----------------------------------------------------------------------
  view.append(el("h2", {}, "Runs"));
  const runsBox = el("div", {});
  view.append(runsBox);
  renderRuns(d);

  // The page used to be a static snapshot — a run finishing while you look at it left a
  // stale hub. Its own run lifecycle events refresh the header chip, health, and runs.
  const onBus = async (e) => {
    const ev = e.detail || {};
    if (!["run_started", "run_finished"].includes(ev.event)) return;
    if (!String(ev.run_id || "").startsWith(`${slug}:`)) return;
    refreshHead();
    if (ev.event === "run_finished") {
      health.reload();
      try { renderRuns(await api(`/api/routines/${slug}`)); } catch { /* keep the old table */ }
    }
  };
  window.addEventListener("rsched-bus", onBus);
  return () => window.removeEventListener("rsched-bus", onBus);

  function renderRuns(d) {
  runsBox.replaceChildren();
  const view = runsBox;
  const rows = (d.runs || []).map((r) => el("tr", {},
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
}
}
