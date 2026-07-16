// Routine detail: schedule, permissions (user-only toggles), budgets, models, origin, and the
// navigable recipe (main.md + stage modules + trait modules), then state & runs.

import { api } from "/static/api.js";
import { deliberationControl } from "/static/components/deliberation.js";
import { confirmDialog } from "/static/components/dialog.js";
import { tagsEditor } from "/static/components/tags.js";
import { md, mdInline } from "/static/md.js";
import { setQuery } from "/static/router.js";
import { scheduleEditor } from "/static/components/schedule.js";
import { permissionsPanel } from "/static/components/permissions.js";
import { recipeNav } from "/static/components/recipenav.js";
import { chip, el, emptyState, fmtDur, fmtTokens, skeleton, toast, when } from "/static/util.js";

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
  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("div", { class: "kicker" }, "routine"),
      el("h1", {}, d.name || slug)),
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

  // -- description (always present; shown here + on the dashboard) ----------------
  const descInput = el("input", { type: "text", value: d.description || "", placeholder: "one-line description",
    style: "width:100%;max-width:640px" });
  view.append(el("h2", {}, "Description"),
    el("div", { class: "panel" },
      el("div", { class: "muted small", style: "margin-bottom:8px" },
        "a one-line summary of what this routine does — shown on the dashboard and here"),
      descInput,
      el("div", { class: "row mt" }, el("button", { class: "btn primary",
        onclick: async () => {
          const v = descInput.value.trim();
          if (!v) { toast("description can't be empty"); return; }
          try { await api(`/api/routines/${slug}`, { method: "PATCH", body: { description: v } }); toast("description saved"); }
          catch (err) { toast(err.message, 4000, { error: true }); }
        } }, "save description"))));

  // -- tags (shared editor — every add/remove saves immediately) --------------------
  view.append(el("h2", {}, "Tags"),
    el("div", { class: "panel" },
      el("div", { class: "muted small", style: "margin-bottom:8px" },
        "freeform labels for filtering on the dashboard (e.g. meta tucks a routine away by ",
        "default) — each change saves immediately"),
      tagsEditor(d.tags, async (next) => {
        await api(`/api/routines/${slug}`, { method: "PATCH", body: { tags: next } });
        toast("tags saved");
      })));

  // -- schedule -------------------------------------------------------------------
  const nextFireLine = el("div", { class: "muted mt small" },
    ...(d.next_fire ? ["next run · ", when(d.next_fire)] : []));
  // saves update the header chip + next-fire IN PLACE — never a page reload
  async function refreshHead() {
    try {
      const nd = await api(`/api/routines/${slug}`);
      chipHost.replaceChildren(runChip(nd));
      nextFireLine.replaceChildren(...(nd.next_fire ? ["next run · ", when(nd.next_fire)] : []));
    } catch { /* cosmetic refresh — the save itself already succeeded */ }
  }
  const sched = scheduleEditor(d.schedule_friendly || { frequency: "manual" }, d.server_tz);
  const enabledBox = el("input", { type: "checkbox", checked: d.enabled || null });
  const improveBox = el("input", { type: "checkbox", checked: d.improve !== false || null });
  view.append(el("h2", {}, "Schedule"),
    el("div", { class: "panel" }, sched.node,
      el("label", { class: "row mt", style: "gap:8px" }, enabledBox, "enabled"),
      el("label", { class: "row mt", style: "gap:8px" }, improveBox,
        el("span", {}, "include in improvement — the routine-improver meta routine visits this routine (on by default)")),
      el("div", { class: "row mt" }, el("button", {
        class: "btn primary",
        onclick: async () => {
          try {
            await api(`/api/routines/${slug}`, { method: "PATCH",
              body: { enabled: enabledBox.checked, improve: improveBox.checked,
                      schedule: { friendly: sched.value() } } });
            toast("schedule saved"); refreshHead();
          } catch (err) { toast(err.message, 4000, { error: true }); }
        },
      }, "save schedule")),
      nextFireLine));

  // -- permissions: conduct docs + machine-enforced capabilities (user-only) --------
  // The server re-applies the activation cascade on save, so the panel re-renders from a
  // fresh detail read IN PLACE — the old full page reload is gone.
  const permHost = el("div", {});
  const buildPermPanel = (perms, caps) => permissionsPanel(perms, caps, {
    onSave: async (payload) => {
      try {
        await api(`/api/routines/${slug}/permissions`, { method: "PUT", body: payload });
        toast("permissions saved");
        const nd = await api(`/api/routines/${slug}`);
        permHost.replaceChildren(buildPermPanel(nd.permissions, nd.capabilities));
      } catch (err) { toast(err.message, 4000, { error: true }); }
    },
  });
  permHost.append(buildPermPanel(d.permissions, d.capabilities));
  view.append(el("h2", {}, "Permissions & capabilities"),
    el("div", { class: "panel" },
      el("div", { class: "muted small", style: "margin-bottom:10px" },
        "what this routine is ALLOWED to do — enforced by the engine on every action. Only you can ",
        "change either column; the routine can never grant itself anything. Takes effect at the next run."),
      permHost));

  // -- budgets (per-run ceilings — every invisible limit, surfaced) -----------------
  const UNLIMITED_BUDGETS = ["max_total_tokens", "max_wall_clock_min", "max_cost"];  // -1 = unlimited
  const BUDGET_FIELDS = [
    ["max_turns", "turns per run", "each model action is one turn; the run is stopped at the cap"],
    ["max_wall_clock_min", "minutes per run", "wall-clock ceiling (time waiting on you is credited back); -1 = unlimited"],
    ["max_total_tokens", "tokens per run", "cumulative input+output tokens; -1 = unlimited (the default — turns bound the run)"],
    ["max_cost", "cost cap per run ($)", "whole-dollar ceiling on real provider spend (reported by metered endpoints like OpenRouter); -1 = unlimited (the default)"],
    ["max_subruns", "sub-workflows per run", "how many parallel children a run may spawn in total"],
    ["max_subrun_depth", "sub-workflow depth", "how deep children may nest (children get half the parent's remainder)"],
    ["ask_timeout_min", "blocking-question timeout (min)", "minutes a blocking decision waits for you before the run continues without it (the question stays open on the Decisions page)"],
  ];
  const budgetInputs = {};
  const budgetRows = BUDGET_FIELDS.map(([key, label, help]) => {
    const input = el("input", { type: "number", min: UNLIMITED_BUDGETS.includes(key) ? "-1" : "0",
      value: String(d.budgets?.[key] ?? ""), style: "width:110px" });
    budgetInputs[key] = input;
    return el("div", { class: "row", style: "margin:5px 0" },
      input,
      el("span", { style: "min-width:220px" }, label),
      el("span", { class: "muted small" }, help));
  });
  view.append(el("h2", {}, "Budgets"),
    el("div", { class: "panel" },
      el("div", { class: "muted small", style: "margin-bottom:8px" },
        "hard per-run ceilings, checked at every turn — the run is told at 85% so it can wind down ",
        "deliberately. Resources, not permissions."),
      ...budgetRows,
      el("div", { class: "row mt" }, el("button", { class: "btn primary",
        onclick: async () => {
          const budgets = {};
          for (const [key, input] of Object.entries(budgetInputs)) {
            const v = parseInt(input.value, 10);
            const unlimitedOk = UNLIMITED_BUDGETS.includes(key) && v === -1;
            if (!Number.isFinite(v) || (v < 1 && !unlimitedOk)) {
              toast(`${key}: needs a positive number${UNLIMITED_BUDGETS.includes(key) ? " (or -1 = unlimited)" : ""}`);
              return;
            }
            budgets[key] = v;
          }
          try { await api(`/api/routines/${slug}`, { method: "PATCH", body: { budgets } });
            toast("budgets saved"); }
          catch (err) { toast(err.message, 4000, { error: true }); }
        } }, "save budgets"))));

  // -- models (per routine: main / subroutine / tool_call / uncensored) ----------
  const MODEL_KINDS = [["main", "the orchestrator loop"], ["subroutine", "spawned sub-workflows"],
                       ["tool_call", "the llm action"],
                       ["uncensored", "a refused llm call is referred here (opt-in)"]];
  const catalog = d.catalog || [];      // catalog model names (see Settings → Models)
  const sysM = d.system_model;          // the system model's catalog name (or null)
  const modelSelects = {};
  const modelRows = MODEL_KINDS.map(([kind, desc]) => {
    const cur = (d.models && d.models[kind]) || "";   // a catalog model NAME, or "" = fallback
    const sel = el("select", {}, [
      el("option", { value: "" }, sysM ? `— system default (${sysM}) —` : "— system default —"),
      ...catalog.map((n) => el("option", { value: n }, n))]);
    sel.value = cur || "";
    modelSelects[kind] = sel;
    return el("div", { class: "row", style: "margin:5px 0" },
      el("span", { class: "ref-tag", style: "min-width:92px;text-align:center" }, kind),
      el("span", { class: "muted small", style: "min-width:150px" }, desc),
      sel);
  });
  const refMonth = d.spend?.current?.referrals || 0;
  // Deliberation: how much thinking lands on paper (the say/notes contract). Saved on
  // release — the next run composes with the new level (a LIVE run is re-leveled from
  // the run view, control.json-scoped).
  const delib = deliberationControl(d.deliberation || "standard", {
    onCommit: async (level) => {
      try { await api(`/api/routines/${slug}`, { method: "PATCH", body: { deliberation: level } });
        toast(`deliberation: ${level} — applies from the next run`); }
      catch (err) { toast(err.message, 4000, { error: true }); }
    },
  });
  view.append(el("h2", {}, "Models"),
    el("div", { class: "panel" },
      el("div", { class: "muted small", style: "margin-bottom:8px" },
        catalog.length
          ? "which catalog model this routine uses for each role — leave on system default to fall back to the system model"
          : "add a model in Settings first"),
      ...modelRows,
      el("div", { class: "row mt", style: "align-items:flex-start" },
        el("span", { class: "ref-tag", style: "min-width:92px;text-align:center" }, "deliberation"),
        el("span", { class: "muted small", style: "min-width:150px" },
          "how much thinking lands on paper"),
        delib.node),
      d.referrals_total
        ? el("div", { class: "muted small mt",
            title: "turns or llm calls the main/tool model refused and the uncensored model answered instead (from the durable usage stream)" },
            `↪ uncensored referrals: ${d.referrals_total} total` + (refMonth ? ` · ${refMonth} this month` : ""))
        : null,
      el("div", { class: "row mt" }, el("button", { class: "btn primary",
        onclick: async () => {
          const models = {};
          for (const [kind, sel] of Object.entries(modelSelects))
            if (sel.value) models[kind] = sel.value;
          try { await api(`/api/routines/${slug}`, { method: "PATCH", body: { models } });
            toast("models saved"); }
          catch (err) { toast(err.message, 4000, { error: true }); }
        } }, "save models"))));

  // -- origin: the library pattern this routine was generated from (provenance only) ----------
  const wf = d.workflow_ref || {};
  view.append(el("h2", {}, "Origin"),
    el("div", { class: "panel" },
      el("span", { class: "ref-tag" }, wf.slug || "hand-authored"),
      el("span", { class: "muted small", style: "margin-left:10px" },
        wf.slug
          ? (wf.in_library
             ? "the library pattern this routine was generated from — its recipe is the routine's OWN now (edit it in the Recipe section below)"
             : "its origin pattern is no longer in this library — the recipe is the routine's OWN (edit it in the Recipe section below)")
          : "written directly, not generated from a library pattern")));

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

  let recipeTree = null;
  let currentFile = query.file || "";
  function renderNav() {
    if (recipeTree) navCol.replaceChildren(recipeNav(recipeTree, openFile, currentFile));
  }
  async function refreshTree() {
    try { recipeTree = await api(`/api/routines/${slug}/recipe`); renderNav(); } catch { /* keep last */ }
  }
  async function openFile(path, heading, silent = false) {
    currentFile = path;
    renderNav();
    let data;
    try { data = await api(`/api/routines/${slug}/file?path=${encodeURIComponent(path)}`); }
    catch (err) { toast(err.message, 4000, { error: true }); return; }
    editorCol.replaceChildren(fileEditorPane(path, data.content, heading));
    if (!silent) { setQuery({ file: path }); editorCol.scrollIntoView({ behavior: "smooth", block: "nearest" }); }
  }
  api(`/api/routines/${slug}/recipe`).then((t) => {
    recipeTree = t; renderNav();
    if (currentFile) openFile(currentFile, null, true);   // restore an open file from the URL
  }).catch((err) => navCol.replaceChildren(el("div", { class: "muted" }, `couldn't load recipe: ${err.message}`)));

  // One file's editor: an edit/preview toggle (rendered markdown via md()), save + commit through
  // /file, and — when opened via a heading in the tree — a scroll to that heading.
  function fileEditorPane(path, content, heading) {
    const ta = el("textarea", { class: "code recipe-ta" }, content || "");
    const preview = el("div", { class: "prose recipe-preview", style: "display:none" });
    const editBtn = el("button", { class: "btn small primary" }, "edit");
    const prevBtn = el("button", { class: "btn small" }, "preview");
    const setMode = (previewing) => {
      if (previewing) preview.replaceChildren(md(ta.value));
      preview.style.display = previewing ? "" : "none";
      ta.style.display = previewing ? "none" : "";
      editBtn.classList.toggle("primary", !previewing);
      prevBtn.classList.toggle("primary", previewing);
    };
    editBtn.onclick = () => setMode(false);
    prevBtn.onclick = () => setMode(true);
    const saveBtn = el("button", { class: "btn primary" }, "save");
    saveBtn.onclick = async () => {
      try {
        await api(`/api/routines/${slug}/file`, { method: "PUT", body: { path, content: ta.value } });
        toast(`${path} saved`); refreshTree();   // headings may have changed
      } catch (err) { toast(err.message, 5000, { error: true }); }
    };
    if (heading) requestAnimationFrame(() => scrollToHeading(ta, heading));
    return el("div", {},
      el("div", { class: "row spread", style: "margin-bottom:8px" },
        el("span", { class: "ref-tag" }, path),
        el("div", { class: "row" }, editBtn, prevBtn)),
      ta, preview,
      el("div", { class: "row mt" }, saveBtn));
  }

  function scrollToHeading(ta, heading) {
    const lines = ta.value.split("\n");
    const needle = heading.trim();
    const idx = lines.findIndex((l) => /^#{1,4}\s/.test(l)
      && l.replace(/^#{1,4}\s+/, "").replace(/`/g, "").trim() === needle);
    if (idx < 0) return;
    const offset = lines.slice(0, idx).reduce((n, l) => n + l.length + 1, 0);
    ta.focus();
    ta.setSelectionRange(offset, offset);
    const lh = parseFloat(getComputedStyle(ta).lineHeight) || 18;
    ta.scrollTop = Math.max(0, (idx - 1) * lh);
  }

  // -- questions ------------------------------------------------------------------
  if (d.questions?.length) {
    const openCount = d.questions.filter((q) => !q.answered).length;
    view.append(el("h2", {}, `Decisions · ${openCount}`),
      el("div", { class: "panel warn" }, d.questions.map((q) =>
        el("div", { class: "row spread", style: "padding:5px 0" },
          el("span", { class: "prose" }, q.answered ? "✓ " : "❓ ", mdInline(q.question)),
          q.answered
            ? chip("answered — queued for next run", "waiting_user")
            : el("a", { class: "btn small primary", href: "#/questions" }, "answer")))));
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
