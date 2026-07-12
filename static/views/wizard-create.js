// New-routine wizard, part 2: the suggest → finalize → building stages (the clarify chat and
// draft stage live in wizard.js). `ctx` carries the stage container plus the shared session
// helpers (cancel, error, building-cleanup registration).

import { api } from "/static/api.js";
import { navigate } from "/static/router.js";
import { scheduleEditor } from "/static/components/schedule.js";
import { busy, el, grantsSummary, toast } from "/static/util.js";

// ---- stage: building (the routine is scaffolding in the background) -------------------------
export function stageBuilding(ctx, wid, snap) {
  ctx.closeTail();
  ctx.clearBuilding();
  ctx.stage.replaceChildren(el("h2", {}, "Building the routine"),
    busy("Building the routine — the model is decomposing the workflow into steps tailored to your "
      + "task. This usually takes a minute or two. You can leave this page; you'll be taken to the "
      + "routine when it's ready, and the banner up top brings you back."));
  let done = false;
  const goTo = (runId, slug) => {
    if (done) return; done = true; ctx.clearBuilding(); ctx.notifyChanged();
    navigate(runId ? `#/run/${runId}` : `#/routine/${slug}`);
  };
  const failed = (msg) => {   // put the user back on the create form to retry
    if (done) return; done = true; ctx.clearBuilding(); ctx.notifyChanged();
    toast(msg, 7000, { error: true }); stageSuggest(ctx, wid);
  };
  const onBus = (e) => {
    const ev = e.detail || {};
    if (ev.wid !== wid) return;
    if (ev.event === "routine_created") goTo(ev.run_id, ev.slug);
    else if (ev.event === "routine_failed") failed(`couldn't build the routine: ${ev.error || "unknown error"}`);
  };
  window.addEventListener("rsched-bus", onBus);   // instant hand-off when the build finishes
  const started = Date.now();
  const poll = setInterval(async () => {          // robust fallback (survives a missed event / reload)
    if (done) return;
    let s;
    try { s = await api(`/api/wizard/${encodeURIComponent(wid)}`); }
    catch { goTo(null, snap?.slug || ""); return; }   // 404 → session archived → the routine exists
    if (s.stage === "done") goTo(s.run_id, s.slug);
    else if (s.stage === "error") failed(`couldn't build the routine: ${s.error || "unknown error"}`);
    else if (Date.now() - started > 300000)
      failed("the build is taking unusually long — it may be stuck. Try creating it again.");
  }, 3000);
  ctx.setBuildingCleanup(() => { clearInterval(poll); window.removeEventListener("rsched-bus", onBus); });
}

// ---- stage: suggest + finalize ----------------------------------------------------------------
export async function stageSuggest(ctx, wid) {
  ctx.closeTail();
  ctx.stage.replaceChildren(busy(
    "Waiting for the model — turning the conversation into a refined instruction and "
    + "matching it to the workflow library…"));
  let data;
  try { data = await api(`/api/wizard/${encodeURIComponent(wid)}/suggest`, { method: "POST" }); }
  catch (err) { ctx.stageError(wid, `clarify run ended without a result: ${err.message}`); return; }
  const wr = data.wizard_result;
  // Editable: what stands in this box at "create routine" is what becomes instruction.md.
  const instrTa = el("textarea", { class: "code", style: "min-height:220px" },
    wr.refined_instruction);
  ctx.stage.replaceChildren(el("div", { class: "row spread" },
    el("h2", {}, "Refined instruction"),
    el("button", { class: "btn small danger", onclick: () => ctx.cancelSession(wid) }, "cancel setup")),
    el("div", { class: "muted small", style: "margin-bottom:4px" },
      "editable — tweak anything before creating; this exact text becomes the routine's instruction"),
    instrTa);

  const picked = { slug: data.suggestions[0]?.slug || "" };
  const picksRow = el("div", { class: "pick-row" });
  const renderPicks = () => {
    picksRow.replaceChildren();
    for (const s of data.suggestions) {
      picksRow.append(el("button", {
        class: `btn ${picked.slug === s.slug ? "primary" : ""}`,
        title: s.reason,
        onclick: () => { picked.slug = s.slug; renderPicks(); },
      }, `${s.slug} (${Math.round(s.confidence * 100)}%)`));
    }
    picksRow.append(genBtn);
  };
  const genBtn = el("button", { class: "btn" }, "✨ generate a new workflow");
  genBtn.onclick = async () => {
    genBtn.disabled = true; genBtn.textContent = "generating…";
    try {
      const r = await api(`/api/wizard/${encodeURIComponent(wid)}/generate-workflow`, { method: "POST",
        body: { hint: data.new_workflow_hint || "" } });
      data.suggestions.unshift({ slug: r.workflow_slug, confidence: 1, reason: "generated draft" });
      picked.slug = r.workflow_slug;
      toast(`draft workflow '${r.workflow_slug}' created in the library`);
    } catch (err) { toast(err.message, 6000, { error: true }); }
    genBtn.disabled = false; genBtn.textContent = "✨ generate a new workflow";
    renderPicks();
  };
  ctx.stage.append(el("h2", {}, "Workflow"));
  if (data.none_fit)   // append conditionally — a bare `null` here renders as the text "null"
    ctx.stage.append(el("div", { class: "muted" }, `suggester: ${data.new_workflow_hint || "nothing fits well"}`));
  ctx.stage.append(picksRow);
  renderPicks();

  // ---- traits + permissions + budgets (preselected per task; all editable here) -------------
  const lib = await api("/api/library").catch(() => ({ traits: [], permissions: [] }));
  const traitBoxes = {};
  const permBoxes = {};
  const pickerRow = (boxes, doc, preset) => {
    const cb = el("input", { type: "checkbox", checked: preset.has(doc.slug) ? "" : null });
    boxes[doc.slug] = cb;
    const grants = grantsSummary(doc.grants);
    return el("label", { class: "row", style: "gap:6px;font-size:12px;margin:2px 0;align-items:baseline" },
      cb, el("strong", { style: "min-width:170px" }, doc.slug),
      el("span", { class: "muted prose" }, doc.summary || "",
        grants ? el("span", { style: "color:var(--warn)" }, ` ▸ ${grants}`) : ""));
  };
  const presetTraits = new Set(data.suggested_traits || lib.default_traits || []);
  const presetPerms = new Set(data.suggested_permissions || lib.default_permissions || []);
  ctx.stage.append(el("h2", {}, "Traits"),
    el("div", { class: "panel" },
      el("div", { class: "muted small", style: "margin-bottom:6px" },
        "reusable practices, ADAPTED into the routine's own files at creation — preselected for this ",
        "task; after creation they belong to the routine (it refines them itself; no toggles later)."),
      ...(lib.traits || []).map((t) => pickerRow(traitBoxes, t, presetTraits))),
    el("h2", {}, "Permissions"),
    el("div", { class: "panel" },
      el("div", { class: "muted small", style: "margin-bottom:6px" },
        "what the routine is ALLOWED to do — enforced by the engine on every action. Preselected ",
        "conservatively for this task; changeable any time on the routine page (only by you)."),
      ...(lib.permissions || []).map((p) => pickerRow(permBoxes, p, presetPerms))));

  const BUDGET_FIELDS = [
    ["max_turns", "turns per run"],
    ["max_wall_clock_min", "minutes per run"],
    ["max_total_tokens", "tokens per run"],
    ["max_subruns", "sub-workflows per run"],
    ["max_subrun_depth", "sub-workflow depth"],
    ["ask_timeout_min", "blocking-question timeout (min)"],
  ];
  const budgetInputs = {};
  ctx.stage.append(el("h2", {}, "Budgets"),
    el("div", { class: "panel" },
      el("div", { class: "muted small", style: "margin-bottom:6px" },
        "hard per-run ceilings (turns, time, tokens, sub-workflows, how long a blocking question ",
        "waits for you). The defaults suit most routines — adjustable here and on the routine page."),
      el("div", { class: "row", style: "flex-wrap:wrap;gap:10px" },
        ...BUDGET_FIELDS.map(([key, label]) => {
          const input = el("input", { type: "number", min: "1", style: "width:110px",
            value: String((lib.default_budgets || {})[key] ?? "") });
          budgetInputs[key] = input;
          return el("label", { class: "field", style: "min-width:170px" },
            el("span", {}, label), input);
        }))));

  // Schedule is routine CONFIG, set here (or later on the routine page) — it is never
  // part of the instruction and never suggested by the model.
  const f = {
    slug: el("input", { type: "text", value: wr.suggested_slug || "" }),
    name: el("input", { type: "text", value: wr.suggested_name || "" }),
    tags: el("input", { type: "text", value: (data.suggested_tags || []).join(", "),
      placeholder: "three tags — reusing existing ones where they fit" }),
  };
  const status = await api("/api/status").catch(() => ({}));
  const sched = scheduleEditor({ frequency: "manual" }, status.server_tz);
  const runNow = el("input", { type: "checkbox", checked: true });
  const create = el("button", { class: "btn primary" }, "create routine");
  create.onclick = async () => {
    if (!picked.slug) { toast("pick a workflow"); return; }
    create.disabled = true;
    try {
      // The build runs in the BACKGROUND (decompose is a slow LLM step) — this returns at once.
      const budgets = {};
      for (const [key, input] of Object.entries(budgetInputs)) {
        const v = parseInt(input.value, 10);
        if (Number.isFinite(v) && v >= 1) budgets[key] = v;
      }
      const r = await api(`/api/wizard/${encodeURIComponent(wid)}/finalize`, { method: "POST", body: {
        slug: f.slug.value.trim(), name: f.name.value.trim() || f.slug.value.trim(),
        workflow_slug: picked.slug, friendly: sched.value(), run_now: runNow.checked,
        instruction: instrTa.value,       // the (possibly edited) refined instruction
        tags: f.tags.value.split(",").map((t) => t.trim()).filter(Boolean),
        traits: Object.entries(traitBoxes).filter(([, b]) => b.checked).map(([s]) => s),
        permissions: Object.entries(permBoxes).filter(([, b]) => b.checked).map(([s]) => s),
        budgets,
      }});
      ctx.notifyChanged();                 // the top banner now shows the build in progress
      stageBuilding(ctx, wid, { slug: r.slug });
    } catch (err) { toast(err.message, 6000, { error: true }); create.disabled = false; }
  };
  ctx.stage.append(el("h2", {}, "Create"),
    el("div", { class: "panel" },
      el("div", { class: "field-row" },
        el("label", { class: "field" }, el("span", {}, "slug"), f.slug),
        el("label", { class: "field" }, el("span", {}, "name"), f.name)),
      el("label", { class: "field" }, el("span", {}, "schedule"), sched.node),
      el("label", { class: "field" }, el("span", {}, "tags"), f.tags),
      el("div", { class: "muted small", style: "margin-top:-2px" },
        "suggested from the existing vocabulary — reused where they fit, new ones only for a genuinely new facet"),
      wr.notes ? el("div", { class: "muted mt prose" }, `wizard notes: ${wr.notes}`) : null,
      el("div", { class: "row mt" },
        el("label", { class: "row", style: "gap:4px" }, runNow, "first run immediately"),
        create)));
}
