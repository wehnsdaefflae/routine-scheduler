// Routine detail: schedule, permissions (user-only toggles), budgets, workflow reference,
// editable instruction / steps / trait files, models, state, runs.

import { api } from "/static/api.js";
import { mdInline } from "/static/md.js";
import { setQuery } from "/static/router.js";
import { scheduleEditor } from "/static/components/schedule.js";
import { permissionsPanel } from "/static/components/permissions.js";
import { busy, chip, el, emptyState, fmtDur, fmtTokens, skeleton, tagChip, toast, when } from "/static/util.js";
import { forgetField } from "/static/formpersist.js";

export async function render(view, slug, query = {}) {
  view.append(skeleton(["35%", "100%", "70%"]));
  let d, st;
  try { [d, st] = await Promise.all([api(`/api/routines/${slug}`), api("/api/status").catch(() => ({}))]); }
  catch (err) { view.replaceChildren(emptyState("✕", `Couldn't load ${slug}`, err.message)); return; }
  view.replaceChildren();
  const llmReady = st.llm_ready !== false;

  const stateChip = d.active_state ? chip(d.active_state, d.active_state)
    : d.enabled ? chip("idle", "idle") : chip("disabled", "disabled");
  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("div", { class: "kicker" }, "routine"),
      el("h1", {}, d.name || slug)),
    el("div", { class: "row" }, stateChip,
      d.active_run
        ? el("a", { class: "btn primary", href: `#/run/${d.active_run}` }, "◉ watch live")
        : el("button", { class: "btn primary", disabled: !llmReady,
            title: llmReady ? "" : "connect an LLM endpoint in Settings first", onclick: runNow }, "▶ run now"),
      el("button", { class: "btn danger", onclick: archive }, "archive"))));
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
    if (!confirm(`Archive "${slug}"? It leaves the scheduler (dir moves to .archive).`)) return;
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

  // -- tags -----------------------------------------------------------------------
  let tags = [...(d.tags || [])];
  const tagsRow = el("div", { class: "tags" });
  const tagInput = el("input", { type: "text", placeholder: "add tag…", style: "width:130px" });
  function renderTags() {
    tagsRow.replaceChildren();
    tags.forEach((t) => tagsRow.append(tagChip(t,
      { onRemove: () => { tags = tags.filter((x) => x !== t); renderTags(); } })));
    tagsRow.append(tagInput);
  }
  function addTag() {
    const v = tagInput.value.trim().toLowerCase().replace(/\s+/g, "-");
    if (v && !tags.includes(v)) { tags.push(v); tagInput.value = ""; forgetField(tagInput); renderTags(); }
    tagInput.focus();
  }
  tagInput.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); addTag(); } };
  renderTags();
  view.append(el("h2", {}, "Tags"),
    el("div", { class: "panel" },
      el("div", { class: "muted small", style: "margin-bottom:8px" },
        "freeform labels for filtering on the dashboard (e.g. meta tucks a routine away by default)"),
      tagsRow,
      el("div", { class: "row mt" },
        el("button", { class: "btn small", onclick: addTag }, "+ add"),
        el("button", {
          class: "btn primary",
          onclick: async () => {
            try { await api(`/api/routines/${slug}`, { method: "PATCH", body: { tags } }); toast("tags saved"); }
            catch (err) { toast(err.message, 4000, { error: true }); }
          },
        }, "save tags"))));

  // -- schedule -------------------------------------------------------------------
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
            toast("schedule saved"); setTimeout(() => location.reload(), 400);
          } catch (err) { toast(err.message, 4000, { error: true }); }
        },
      }, "save schedule")),
      d.next_fire ? el("div", { class: "muted mt small" }, "next run · ", when(d.next_fire)) : null));

  // -- workflow = the routine's OWN main.md (self-contained; generated from a recipe) ----------
  // Provenance honesty: "hand-authored" when there is no origin pattern, and an explicit
  // note when the claimed origin is no longer (or never was) in this instance's library.
  const wf = d.workflow_ref || {};
  view.append(el("h2", {}, "Workflow (main.md)"),
    el("div", { class: "panel row spread" },
      el("div", {},
        el("span", { class: "ref-tag" }, wf.slug || "hand-authored"),
        el("span", { class: "muted small", style: "margin-left:10px" },
          wf.slug
            ? (wf.in_library
               ? "the recipe this routine was born from — main.md is the routine's OWN now, editable below"
               : "its origin pattern is not in this library (anymore) — main.md is the routine's OWN, editable below")
            : "written directly, not generated from a library pattern — main.md is the routine's OWN, editable below")),
      el("button", { class: "btn small",
        onclick: () => editFile("main.md", "main.md — the routine's workflow") }, "edit main.md")));

  // -- permissions: conduct docs + machine-enforced capabilities (user-only) --------
  view.append(el("h2", {}, "Permissions & capabilities"),
    el("div", { class: "panel" },
      el("div", { class: "muted small", style: "margin-bottom:10px" },
        "what this routine is ALLOWED to do — enforced by the engine on every action. Only you can ",
        "change either column; the routine can never grant itself anything. Takes effect at the next run."),
      permissionsPanel(d.permissions, d.capabilities, {
        onSave: async (payload) => {
          try { await api(`/api/routines/${slug}/permissions`, { method: "PUT", body: payload });
            toast("permissions saved"); setTimeout(() => location.reload(), 400); }
          catch (err) { toast(err.message, 4000, { error: true }); }
        },
      })));

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

  // -- models (per routine: main / subroutine / tool_call) -----------------------
  const MODEL_KINDS = [["main", "the orchestrator loop"], ["subroutine", "spawned sub-workflows"],
                       ["tool_call", "the llm action"]];
  const endpointNames = d.endpoints || [];
  const sysM = d.system_model;
  const modelInputs = {};
  const modelRows = MODEL_KINDS.map(([kind, desc]) => {
    const cur = (d.models && d.models[kind]) || null;
    const epSel = el("select", {}, [el("option", { value: "" }, "— system default —"),
      ...endpointNames.map((n) => el("option", { value: n }, n))]);
    if (cur?.endpoint) epSel.value = cur.endpoint;
    const modelIn = el("input", { type: "text", value: cur?.model || "",
      placeholder: sysM ? `${sysM.endpoint} / ${sysM.model}` : "model id", style: "width:200px" });
    modelInputs[kind] = { epSel, modelIn };
    return el("div", { class: "row", style: "margin:5px 0" },
      el("span", { class: "ref-tag", style: "min-width:92px;text-align:center" }, kind),
      el("span", { class: "muted small", style: "min-width:150px" }, desc),
      epSel, modelIn);
  });
  view.append(el("h2", {}, "Models"),
    el("div", { class: "panel" },
      el("div", { class: "muted small", style: "margin-bottom:8px" },
        endpointNames.length
          ? "which endpoint + model this routine uses for each role — leave blank to fall back to the system model"
          : "add an endpoint in Settings first"),
      ...modelRows,
      el("div", { class: "row mt" }, el("button", { class: "btn primary",
        onclick: async () => {
          const models = {};
          for (const [kind, { epSel, modelIn }] of Object.entries(modelInputs)) {
            const ep = epSel.value.trim(), m = modelIn.value.trim();
            if (ep && m) models[kind] = { endpoint: ep, model: m };
            else if (ep || m) { toast(`${kind}: set both endpoint and model, or clear both`); return; }
          }
          try { await api(`/api/routines/${slug}`, { method: "PATCH", body: { models } });
            toast("models saved"); setTimeout(() => location.reload(), 400); }
          catch (err) { toast(err.message, 4000, { error: true }); }
        } }, "save models"))));

  // -- seed (the instruction) → compiled step modules --------------------------
  // The instruction is the SEED: the workflow is compiled against it into main.md + the step
  // modules. Editing the seed does NOT recompile on its own; the drift notes surface the two ways
  // seed and steps fall out of sync (seed edited without recompiling ↔ steps changed under it,
  // e.g. by the routine-improver, leaving the seed stale).
  const seed = d.seed || { tracked: false, instruction: false, steps: false };
  const canRecompile = !!d.recompilable;
  let recompileCleanup = null;   // stops the in-flight recompile poll + bus listener on teardown
  const recompileStatus = el("span", { class: "muted small", style: "margin-left:8px" });
  const recompileBtn = el("button", {
    class: "btn primary", disabled: !canRecompile || !llmReady,
    title: !wf.slug ? "hand-authored — no source workflow to recompile from"
      : !canRecompile ? "its origin workflow isn't in this library — nothing to recompile from"
        : !llmReady ? "connect an LLM endpoint in Settings first" : "",
  }, "⟳ recompile into steps");
  recompileBtn.onclick = startRecompile;

  const driftNotes = el("div", {});
  if (seed.instruction) {
    driftNotes.append(el("div", { class: "panel warn", style: "margin-bottom:8px" },
      "⟳ The seed has changed since the steps were last compiled — the next run still follows the ",
      el("strong", {}, "old"), " step modules. Recompile to regenerate them from the seed."));
  }
  if (seed.steps) {
    driftNotes.append(el("div", { class: "panel warn", style: "margin-bottom:8px" },
      "✎ The step modules have changed since they were compiled from this seed (hand edits, or the ",
      "routine-improver). The seed no longer describes what the routine actually does — ",
      el("strong", {}, "recompiling will overwrite those step edits.")));
  }
  if (!seed.tracked) {
    driftNotes.append(el("div", { class: "muted small", style: "margin-bottom:8px" },
      "no compile baseline yet — recompile once to start tracking seed ↔ steps drift."));
  }

  view.append(el("h2", {}, "Seed"));
  view.append(el("div", { class: "panel" },
    el("div", { class: "muted small", style: "margin-bottom:8px" },
      "the task in plain language — the ", el("strong", {}, "seed"), " the workflow (",
      el("span", { class: "ref-tag" }, wf.slug || "hand-authored"),
      ") is compiled from into the step modules below. Editing it does not recompile automatically."),
    driftNotes,
    el("div", { class: "row" }, recompileBtn, recompileStatus)));
  view.append(docEditor("Seed instruction (the task)", d.instruction, async (content) => {
    await api(`/api/routines/${slug}/instruction`, { method: "PUT", body: { content } });
  }));
  // A recompile runs on the SERVER (background) — leaving the page never aborts it. If one is
  // already in flight when the page loads (started here, then navigated away and back), resume the
  // progress indicator instead of showing an idle button.
  api(`/api/routines/${slug}/recompile`).then((r) => {
    if (r?.state === "building") { recompileBtn.disabled = true; watchRecompile(); }
  }).catch(() => {});

  function watchRecompile() {
    // Poll + live bus hand-off until the background recompile finishes. The stop fn is parked in
    // recompileCleanup so render's teardown (navigation) ends the WATCH — never the server run.
    recompileCleanup?.();
    recompileStatus.replaceChildren(busy("recompiling — decomposing the workflow into steps (a minute or two)…"));
    let done = false;
    const stop = () => { window.removeEventListener("rsched-bus", onBus); clearInterval(poll); recompileCleanup = null; };
    const finish = (ok, msg) => {
      if (done) return; done = true; stop();
      if (ok) { toast("recompiled from the seed"); location.reload(); }
      else { toast(msg || "recompile failed", 7000, { error: true }); recompileBtn.disabled = false; recompileStatus.replaceChildren(); }
    };
    const onBus = (e) => {
      const ev = e.detail || {}; if (ev.slug !== slug) return;
      if (ev.event === "routine_recompiled") finish(true);
      else if (ev.event === "routine_recompile_failed") finish(false, ev.error);
    };
    window.addEventListener("rsched-bus", onBus);   // instant hand-off when the recompile finishes
    const started = Date.now();
    const poll = setInterval(async () => {          // robust fallback (survives a missed event)
      if (done) return;
      let s;
      try { s = await api(`/api/routines/${slug}/recompile`); } catch { return; }
      if (s.state === "done") finish(true);
      else if (s.state === "error" || s.state === "stale") finish(false, s.error);
      else if (Date.now() - started > 300000) finish(false, "the recompile is taking unusually long — it may be stuck");
    }, 3000);
    recompileCleanup = () => { if (!done) stop(); };   // stop watching; the server recompile runs on
  }

  async function startRecompile() {
    const warn = seed.steps
      ? "The step modules have changed since they were compiled from this seed — recompiling "
        + "regenerates them from the current seed and OVERWRITES those changes (including any by "
        + "the routine-improver).\n\nContinue?"
      : "Recompiling regenerates main.md and the step modules from the current seed instruction, "
        + "replacing the current steps.\n\nContinue?";
    if (!confirm(warn)) return;
    recompileBtn.disabled = true;
    try { await api(`/api/routines/${slug}/recompile`, { method: "POST" }); }
    catch (err) { toast(err.message, 5000, { error: true }); recompileBtn.disabled = false; return; }
    watchRecompile();
  }

  const stepFiles = (d.files?.steps) || [];
  view.append(el("h2", {}, "Step modules"),
    el("div", { class: "panel" },
      el("div", { class: "muted small", style: "margin-bottom:8px" },
        "compiled from the seed above — edit freely; the routine-improver may also refine these. ",
        "Changes here make the seed drift, and a recompile would overwrite them."),
      stepFiles.length
        ? el("div", { class: "row" }, stepFiles.map((n) =>
            el("button", { class: "btn small", onclick: () => editFile(`steps/${n}`, n) }, n)))
        : el("div", { class: "muted" }, "none — this recipe keeps its whole flow in main.md")));

  // -- traits: the routine's own practice modules (adapted in at creation) ----------
  const traitFiles = (d.files?.traits) || [];
  view.append(el("h2", {}, "Practice modules (traits)"),
    el("div", { class: "panel" },
      el("div", { class: "muted small", style: "margin-bottom:8px" },
        "reusable practices adapted into this routine at creation — its OWN files now, referenced ",
        "from main.md and refined by the routine itself over time. Not toggles: change them like ",
        "any other routine file."),
      traitFiles.length
        ? el("div", { class: "row" }, traitFiles.map((n) =>
            el("button", { class: "btn small", onclick: () => editFile(`traits/${n}`, n) }, n)))
        : el("div", { class: "muted" }, "none adopted at creation")));

  const fileEditor = el("div", {});
  view.append(fileEditor);

  // The open file-editor is addressable as #/routine/<slug>?file=<path>, so a reload / shared link
  // reopens the exact file. `silent` skips the URL write + scroll when we're restoring from the URL.
  async function editFile(path, label, silent = false) {
    let data;
    try { data = await api(`/api/routines/${slug}/file?path=${encodeURIComponent(path)}`); }
    catch (err) { toast(err.message, 4000, { error: true }); return; }
    fileEditor.replaceChildren();
    const node = docEditor(label || path, data.content, async (content) => {
      await api(`/api/routines/${slug}/file`, { method: "PUT", body: { path, content } });
    });
    fileEditor.append(node);
    if (!silent) { setQuery({ file: path }); node.scrollIntoView({ behavior: "smooth", block: "center" }); }
  }

  if (query.file) editFile(query.file, query.file, true);   // restore an open editor from the URL

  function docEditor(label, content, save) {
    const ta = el("textarea", { class: "code" }, content || "");
    const btn = el("button", { class: "btn primary" }, "save");
    btn.onclick = async () => {
      try { await save(ta.value); toast(`${label} saved`); }
      catch (err) { toast(err.message, 5000, { error: true }); }
    };
    return el("div", { class: "panel mt" },
      el("div", { class: "muted small", style: "margin-bottom:8px" }, label),
      ta, el("div", { class: "row mt" }, btn));
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

  // Teardown (called by the router on navigation): stop watching an in-flight recompile. The
  // recompile itself keeps running on the server — leaving the page never aborts it.
  return () => { recompileCleanup?.(); };
}
