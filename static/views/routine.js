// Routine detail: schedule, permissions (user-only toggles), budgets, workflow reference,
// editable instruction / steps / trait files, models, state, runs.

import { api } from "/static/api.js";
import { mdInline } from "/static/md.js";
import { setQuery } from "/static/router.js";
import { scheduleEditor } from "/static/components/schedule.js";
import { chip, el, emptyState, fmtDur, fmtTokens, grantsSummary, skeleton, tagChip, toast, when } from "/static/util.js";

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
    if (v && !tags.includes(v)) { tags.push(v); tagInput.value = ""; renderTags(); }
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
  view.append(el("h2", {}, "Schedule"),
    el("div", { class: "panel" }, sched.node,
      el("label", { class: "row mt", style: "gap:8px" }, enabledBox, "enabled"),
      el("div", { class: "row mt" }, el("button", {
        class: "btn primary",
        onclick: async () => {
          try {
            await api(`/api/routines/${slug}`, { method: "PATCH",
              body: { enabled: enabledBox.checked, schedule: { friendly: sched.value() } } });
            toast("schedule saved"); setTimeout(() => location.reload(), 400);
          } catch (err) { toast(err.message, 4000, { error: true }); }
        },
      }, "save schedule")),
      d.next_fire ? el("div", { class: "muted mt small" }, "next run · ", when(d.next_fire)) : null));

  // -- workflow = the routine's OWN main.md (self-contained; generated from a recipe) ----------
  view.append(el("h2", {}, "Workflow (main.md)"),
    el("div", { class: "panel row spread" },
      el("div", {},
        el("span", { class: "ref-tag" }, d.workflow_ref?.slug || "—"),
        el("span", { class: "muted small", style: "margin-left:10px" },
          "the recipe this routine was born from — main.md is the routine's OWN now, editable below")),
      el("button", { class: "btn small",
        onclick: () => editFile("main.md", "main.md — the routine's workflow") }, "edit main.md")));

  // -- permissions (user-only toggles; enforced by the engine per action) ----------
  const boxes = {};
  const permRows = (d.permissions || []).map((p) => {
    const box = el("input", { type: "checkbox", checked: p.active ? "" : null });
    boxes[p.slug] = box;
    const grants = grantsSummary(p.grants);
    return el("label", { class: "toggle-row" }, box,
      el("div", {},
        el("div", { class: "t-title" }, p.slug),
        el("div", { class: "muted prose small" }, p.summary || ""),
        grants ? el("div", { class: "small", style: "color:var(--warn)" }, `▸ ${grants}`) : null));
  });
  view.append(el("h2", {}, "Permissions"),
    el("div", { class: "panel" },
      el("div", { class: "muted small", style: "margin-bottom:8px" },
        "what this routine is ALLOWED to do — enforced by the engine on every action. Only you can ",
        "change these; the routine can never grant itself anything. Takes effect at the next run."),
      permRows.length ? permRows : el("div", { class: "muted" }, "no permissions in the library"),
      el("div", { class: "row mt" }, el("button", {
        class: "btn primary",
        onclick: async () => {
          const active = Object.entries(boxes).filter(([, b]) => b.checked).map(([s]) => s);
          try { await api(`/api/routines/${slug}/permissions`, { method: "PUT", body: { active } });
            toast("permissions saved"); setTimeout(() => location.reload(), 400); }
          catch (err) { toast(err.message, 4000, { error: true }); }
        },
      }, "save permissions"))));

  // -- budgets (per-run ceilings — every invisible limit, surfaced) -----------------
  const BUDGET_FIELDS = [
    ["max_turns", "turns per run", "each model action is one turn; the run is stopped at the cap"],
    ["max_wall_clock_min", "minutes per run", "wall-clock ceiling (time waiting on you is credited back)"],
    ["max_total_tokens", "tokens per run", "cumulative input+output tokens; the prompt is re-sent every turn"],
    ["max_subruns", "sub-workflows per run", "how many parallel children a run may spawn in total"],
    ["max_subrun_depth", "sub-workflow depth", "how deep children may nest (children get half the parent's remainder)"],
    ["ask_timeout_h", "blocking-question timeout (h)", "hours a blocking decision waits for you before the run continues without it (the question stays open on the Decisions page)"],
  ];
  const budgetInputs = {};
  const budgetRows = BUDGET_FIELDS.map(([key, label, help]) => {
    const input = el("input", { type: "number", min: "0", value: String(d.budgets?.[key] ?? ""),
      style: "width:110px" });
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
            if (!Number.isFinite(v) || v < 1) { toast(`${key}: needs a positive number`); return; }
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

  // -- editable files: instruction + steps -------------------------------------
  view.append(el("h2", {}, "Instruction"));
  view.append(docEditor("Instruction (the task)", d.instruction, async (content) => {
    await api(`/api/routines/${slug}/instruction`, { method: "PUT", body: { content } });
  }));

  const stepFiles = (d.files?.steps) || [];
  view.append(el("h2", {}, "Step modules"),
    el("div", { class: "panel" },
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
    view.append(el("h2", {}, `Decisions · ${d.questions.length}`),
      el("div", { class: "panel warn" }, d.questions.map((q) =>
        el("div", { class: "row spread", style: "padding:5px 0" },
          el("span", { class: "prose" }, "❓ ", mdInline(q.question)),
          el("a", { class: "btn small primary", href: "#/questions" }, "answer")))));
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
