// New-routine setup panel — the run-page continuation of a clarify session (D11: the
// bespoke wizard view is retired; #/run/clarification:<ts> IS the session surface).
// While the clarify run is live the panel is a slim frame — the standard run page already
// carries the chat (transcript, question form, composer). Once the run ends, the panel
// becomes the suggest → create → building flow over the /api/wizard session endpoints.
// run.js mounts it only when a live session stands behind the run (404 → plain run view).

import { api } from "/static/api.js";
import { deliberationControl } from "/static/components/deliberation.js";
import { navigate } from "/static/router.js";
import { scheduleEditor } from "/static/components/schedule.js";
import { busy, el, requiresSummary, toast } from "/static/util.js";
import { TERMINAL } from "/static/states.js";

// Tell app.js the session advanced / was canceled / finalized so the setup banner updates.
const notifyChanged = () => window.dispatchEvent(new CustomEvent("rsched-wizard-changed"));

export async function createSetupPanel(host, { ts }) {
  const wid = `.wizard-${ts}`;
  const snapshot = () => api(`/api/wizard/${encodeURIComponent(wid)}`);
  let snap;
  try { snap = await snapshot(); }
  catch { return null; }   // no session behind this run (archived long ago) — plain run view

  let stage = "";          // what the panel currently shows; "loading" gates re-entry
  let lastSnap = snap;
  let buildingCleanup = null;   // stops the background-build poll + bus listener
  const clearBuilding = () => { if (buildingCleanup) { buildingCleanup(); buildingCleanup = null; } };

  async function cancelSession() {
    clearBuilding();
    try { await api(`/api/wizard/${encodeURIComponent(wid)}`, { method: "DELETE" }); } catch { /* already gone */ }
    notifyChanged();
    navigate("#/new-routine");
  }

  const cancelBtn = () =>
    el("button", { class: "btn small danger", onclick: cancelSession }, "cancel setup");

  function done(runId, slug) {
    clearBuilding();
    notifyChanged();
    navigate(runId ? `#/run/${runId}` : `#/routine/${slug}`);
  }

  function apply(s) {
    lastSnap = s;
    if (s.stage === "done") { done(s.run_id, s.slug); return; }
    if (s.stage === "building") { if (stage !== "building") renderBuilding(s); return; }
    clearBuilding();
    if (s.stage === "suggest") renderSuggest();
    else if (s.stage === "error") {
      renderError(s.error ? `couldn't build the routine: ${s.error}`
                          : "The clarification run ended without a result.", s.draft_full);
    } else renderChat(s);
  }

  async function refresh() {
    try { apply(await snapshot()); }
    catch { stage = "gone"; host.replaceChildren(); }   // canceled/archived underneath us
  }

  // ---- stage: chat (the run page carries the conversation; this is just the frame) ----------
  function renderChat(s) {
    stage = "chat";
    host.replaceChildren(el("div", { class: "panel mt" },
      el("div", { class: "row spread" },
        el("strong", {}, "◇ New-routine setup — clarification in progress"),
        cancelBtn()),
      el("div", { class: "muted small mt prose" },
        "This run is the clarify chat for a routine you're creating: answer its questions ",
        "as they appear below. When the chat finishes, the create form takes over here."),
      s.alive === false   // process died (e.g. across a restart) mid-conversation
        ? el("div", { class: "panel warn mt" },
            "This session's clarification process is no longer running, so answers won't be ",
            "picked up. Cancel the setup and start over.")
        : null));
  }

  // ---- stage: error (clarify dead end / failed build) ----------------------------------------
  function renderError(msg, draft) {
    stage = "error";
    const row = el("div", { class: "row mt" });
    if (draft) {
      // the draft survives the dead end — retry runs a fresh clarification with the same text
      const retry = el("button", { class: "btn small primary" }, "retry with the same draft");
      retry.onclick = async () => {
        retry.disabled = true;
        try {
          await api(`/api/wizard/${encodeURIComponent(wid)}`, { method: "DELETE" }).catch(() => {});
          const r = await api("/api/wizard/start", { method: "POST", body: { draft } });
          notifyChanged();
          navigate(r.run_id ? `#/run/${r.run_id}` : "#/new-routine");
        } catch (err) { toast(err.message, 6000, { error: true }); retry.disabled = false; }
      };
      row.append(retry);
    }
    row.append(el("button", { class: "btn small", onclick: cancelSession }, "start over"));
    host.replaceChildren(el("div", { class: "panel err mt" }, msg, row));
  }

  // ---- stage: building (the routine is scaffolding in the background) ------------------------
  function renderBuilding(s) {
    stage = "building";
    clearBuilding();
    host.replaceChildren(el("div", { class: "panel mt" },
      el("h2", {}, "Building the routine"),
      busy("Building the routine — the model is decomposing the workflow into stages tailored to "
        + "your task. This usually takes a minute or two. You can leave this page; you'll be "
        + "taken to the routine when it's ready, and the banner up top brings you back.")));
    let finished = false;
    const goTo = (runId, slug) => { if (!finished) { finished = true; done(runId, slug); } };
    const failed = (msg) => {   // put the user back on the create form to retry
      if (finished) return;
      finished = true; clearBuilding(); notifyChanged();
      toast(msg, 7000, { error: true }); renderSuggest();
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
      if (finished) return;
      let cur;
      try { cur = await snapshot(); }
      catch { goTo(null, s?.slug || ""); return; }  // 404 → session archived → the routine exists
      if (cur.stage === "done") goTo(cur.run_id, cur.slug);
      else if (cur.stage === "error") failed(`couldn't build the routine: ${cur.error || "unknown error"}`);
      else if (Date.now() - started > 300000)
        failed("the build is taking unusually long — it may be stuck. Try creating it again.");
    }, 3000);
    buildingCleanup = () => { clearInterval(poll); window.removeEventListener("rsched-bus", onBus); };
  }

  // ---- stage: suggest + create ----------------------------------------------------------------
  async function renderSuggest() {
    stage = "suggest";
    host.replaceChildren(el("div", { class: "panel mt" }, busy(
      "Waiting for the model — turning the conversation into a refined instruction and "
      + "matching it to the workflow library…")));
    let data;
    try { data = await api(`/api/wizard/${encodeURIComponent(wid)}/suggest`, { method: "POST" }); }
    catch (err) {
      renderError(`clarify run ended without a result: ${err.message}`, lastSnap?.draft_full);
      return;
    }
    const wr = data.wizard_result;
    const box = el("div", { class: "mt" });
    // Editable: what stands in this box at "create routine" is what becomes instruction.md.
    const instrTa = el("textarea", { class: "code", style: "min-height:220px" },
      wr.refined_instruction);
    box.append(el("div", { class: "row spread" },
      el("h2", {}, "Refined instruction"), cancelBtn()),
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
    box.append(el("h2", {}, "Workflow"));
    if (data.none_fit)   // append conditionally — a bare `null` here renders as the text "null"
      box.append(el("div", { class: "muted" }, `suggester: ${data.new_workflow_hint || "nothing fits well"}`));
    box.append(picksRow);
    renderPicks();

    // ---- traits + permissions + budgets (preselected per task; all editable here) -------------
    const lib = await api("/api/library").catch(() => ({ traits: [], permissions: [] }));
    const traitBoxes = {};
    const permBoxes = {};
    const pickerRow = (boxes, doc, preset) => {
      const cb = el("input", { type: "checkbox", checked: preset.has(doc.slug) ? "" : null });
      boxes[doc.slug] = cb;
      const req = requiresSummary(doc.requires);
      return el("label", { class: "row", style: "gap:6px;font-size:12px;margin:2px 0;align-items:baseline" },
        cb, el("strong", { style: "min-width:170px" }, doc.slug),
        el("span", { class: "muted prose" }, doc.summary || "",
          req ? el("span", { style: "color:var(--warn)" }, ` ▸ ${req}`) : ""));
    };
    const presetTraits = new Set(data.suggested_traits || lib.default_traits || []);
    const presetPerms = new Set(data.suggested_permissions || lib.default_permissions || []);
    box.append(el("h2", {}, "Traits"),
      el("div", { class: "panel" },
        el("div", { class: "muted small", style: "margin-bottom:6px" },
          "reusable practices, ADAPTED into the routine's own files at creation — preselected for this ",
          "task; after creation they belong to the routine (it refines them itself; no toggles later)."),
        ...(lib.traits || []).map((t) => pickerRow(traitBoxes, t, presetTraits))),
      el("h2", {}, "Permissions"),
      el("div", { class: "panel" },
        el("div", { class: "muted small", style: "margin-bottom:6px" },
          "conduct docs — each switches on the capabilities it needs (engine-enforced on every ",
          "action). Preselected conservatively for this task; both layers are tunable any time ",
          "on the routine page (only by you)."),
        ...(lib.permissions || []).map((p) => pickerRow(permBoxes, p, presetPerms))));

    const UNLIMITED_BUDGETS = ["max_total_tokens", "max_wall_clock_min", "max_cost"];  // -1 = unlimited
    const BUDGET_FIELDS = [
      ["max_turns", "turns per run"],
      ["max_wall_clock_min", "minutes per run (-1 = unlimited)"],
      ["max_total_tokens", "tokens per run (-1 = unlimited)"],
      ["max_cost", "cost cap $ per run (-1 = unlimited)"],
      ["max_subruns", "sub-workflows per run"],
      ["max_subrun_depth", "sub-workflow depth"],
      ["ask_timeout_min", "blocking-question timeout (min)"],
    ];
    const budgetInputs = {};
    box.append(el("h2", {}, "Budgets"),
      el("div", { class: "panel" },
        el("div", { class: "muted small", style: "margin-bottom:6px" },
          "hard per-run ceilings (turns, time, tokens, sub-workflows, how long a blocking question ",
          "waits for you). The defaults suit most routines — adjustable here and on the routine page."),
        el("div", { class: "row", style: "flex-wrap:wrap;gap:10px" },
          ...BUDGET_FIELDS.map(([key, label]) => {
            const input = el("input", { type: "number", style: "width:110px",
              min: UNLIMITED_BUDGETS.includes(key) ? "-1" : "1",   // -1 = unlimited (tokens/time/cost)
              value: String((lib.default_budgets || {})[key] ?? "") });
            budgetInputs[key] = input;
            return el("label", { class: "field", style: "min-width:170px" },
              el("span", {}, label), input);
          }))));

    // Deliberation — suggested per task (how judgment-heavy it is), user-adjustable here
    // and on the routine page; mid-run from the run view.
    const delib = deliberationControl(
      data.suggested_deliberation || lib.default_deliberation || "standard");
    box.append(el("h2", {}, "Deliberation"),
      el("div", { class: "panel" },
        el("div", { class: "muted small", style: "margin-bottom:6px" },
          "how much of the model's thinking lands on paper as it works — suggested from the ",
          "task; raise it for judgment-heavy work, lower it for mechanical pipelines."),
        delib.node));

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
          if (Number.isFinite(v) && (v >= 1 || (UNLIMITED_BUDGETS.includes(key) && v === -1)))
            budgets[key] = v;
        }
        const r = await api(`/api/wizard/${encodeURIComponent(wid)}/finalize`, { method: "POST", body: {
          slug: f.slug.value.trim(), name: f.name.value.trim() || f.slug.value.trim(),
          workflow_slug: picked.slug, friendly: sched.value(), run_now: runNow.checked,
          instruction: instrTa.value,       // the (possibly edited) refined instruction
          tags: f.tags.value.split(",").map((t) => t.trim()).filter(Boolean),
          traits: Object.entries(traitBoxes).filter(([, b]) => b.checked).map(([s]) => s),
          permissions: Object.entries(permBoxes).filter(([, b]) => b.checked).map(([s]) => s),
          budgets,
          deliberation: delib.value,
        }});
        notifyChanged();                 // the top banner now shows the build in progress
        renderBuilding({ slug: r.slug });
      } catch (err) { toast(err.message, 6000, { error: true }); create.disabled = false; }
    };
    box.append(el("h2", {}, "Create"),
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
    if (stage === "suggest") host.replaceChildren(box);   // superseded (e.g. flipped back to chat)?
  }

  apply(snap);

  return {
    // The run page drives the live→terminal transition: when the clarify run ends, the
    // create form takes over; a resumed (conversed-with) run flips the frame back to chat.
    onRunState(state) {
      if (TERMINAL.has(state) && stage === "chat") { stage = "loading"; refresh(); }
      else if (!TERMINAL.has(state) && (stage === "suggest" || stage === "error"))
        renderChat(lastSnap || {});
    },
    destroy: clearBuilding,
  };
}
