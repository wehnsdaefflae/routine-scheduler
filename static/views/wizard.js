// New-routine wizard: draft → clarify chat (a real engine run) → workflow pick → finalize.
//
// Fully URL-driven and resumable: a live session lives at #/wizard/{wid}. Navigating away or
// reloading mid-creation never loses your place — the stage is reconstructed from the backend
// (/api/wizard/{wid}), which derives it from what the clarify run has produced on disk. The
// draft stage (#/wizard) also lists any in-flight sessions so you can resume instead of forking.

import { api, sse } from "/static/api.js";
import { navigate } from "/static/router.js";
import { createTranscript } from "/static/components/transcript.js";
import { busy, el, scheduleEditor, toast } from "/static/util.js";

// Tell app.js a session started / was canceled / finalized so the setup banner updates at once.
const notifyChanged = () => window.dispatchEvent(new CustomEvent("rsched-wizard-changed"));

export async function render(view, resumeWid) {
  view.append(el("h1", {}, "New routine"));
  const st = await api("/api/status").catch(() => ({}));
  if (st.llm_ready === false) {
    view.append(el("div", { class: "panel", style: "border-color:var(--warn)" },
      el("strong", {}, "No model connected"),
      el("div", { class: "muted mt" },
        "Creating a routine runs a clarification through your LLM. Set the ",
        el("code", {}, "system model"), " in ", el("a", { href: "#/settings" }, "Settings"), " first.")));
    return;
  }
  const stage = el("div", {});
  view.append(stage);
  let source = null;
  const closeSource = () => { if (source) { try { source.close(); } catch {} source = null; } };
  let buildingCleanup = null;   // stops the background-build poll + bus listener
  const clearBuilding = () => { if (buildingCleanup) { buildingCleanup(); buildingCleanup = null; } };

  if (resumeWid) resumeSession(resumeWid);   // #/wizard/{wid} → reconstruct the live session
  else stageDraft();

  // ---- resume: fetch the session snapshot and jump to the right stage -----------------------
  async function resumeSession(wid) {
    stage.innerHTML = "";
    stage.append(busy("Reconnecting to your setup session…"));
    let snap;
    try { snap = await api(`/api/wizard/${encodeURIComponent(wid)}`); }
    catch {
      stage.innerHTML = "";
      stage.append(el("div", { class: "panel", style: "border-color:var(--warn)" },
        "This setup session is no longer available.",
        el("div", { class: "row mt" },
          el("button", { class: "btn small primary", onclick: () => navigate("#/wizard") }, "start over"))));
      return;
    }
    if (snap.stage === "building") stageBuilding(wid, snap);
    else if (snap.stage === "done") navigate(snap.run_id ? `#/run/${snap.run_id}` : `#/routine/${snap.slug}`);
    else if (snap.stage === "suggest") stageSuggest(wid);
    else if (snap.stage === "error")
      stageError(wid, snap.error ? `couldn't build the routine: ${snap.error}` : "The clarification run ended without a result.");
    else stageChat(wid, snap);
  }

  // ---- stage: draft --------------------------------------------------------------------------
  function stageDraft() {
    closeSource();
    stage.innerHTML = "";
    const resumeBox = el("div", {});     // filled with any in-flight sessions to resume
    const ta = el("textarea", { class: "code", style: "min-height:160px",
      placeholder: "Describe the TASK the routine should do, in your own words — not when it runs.\n\ne.g. Collect new AI-agent papers from arxiv and keep a reading list with one-line takes." });
    const fragBox = el("div", { class: "mt" }, el("div", { class: "muted", style: "font-size:12px" }, "Standards — loading…"));
    const chosen = () => Array.from(fragBox.querySelectorAll("input:checked")).map((c) => c.dataset.slug);
    const go = el("button", { class: "btn primary" }, "start clarification");
    go.onclick = async () => {
      if (!ta.value.trim()) return;
      go.disabled = true;
      try {
        const r = await api("/api/wizard/start", { method: "POST", body: { draft: ta.value, fragments: chosen() } });
        notifyChanged();
        navigate(`#/wizard/${r.wid}`);       // the session's URL is now the source of truth
      } catch (err) { toast(err.message); go.disabled = false; }
    };
    stage.append(resumeBox, el("div", { class: "panel" },
      el("div", { class: "muted", style: "margin-bottom:8px" },
        "Describe the task in your own words. The wizard asks a few clarifying questions, then builds the ",
        "routine. Below, choose its standards — reusable habits it follows every run (keeping a LEDGER, ",
        "self-auditing, safe tool use). You can change these, its schedule and its models afterwards."),
      ta, fragBox, el("div", { class: "row mt" }, go)));

    // Surface any in-flight sessions so the user resumes instead of starting a second one.
    api("/api/wizard").then((list) => {
      if (!Array.isArray(list) || !list.length) return;
      resumeBox.append(el("div", { class: "panel", style: "border-color:var(--warn);margin-bottom:14px" },
        el("strong", {}, "Setup already in progress"),
        el("div", { class: "muted", style: "font-size:12.5px;margin:4px 0 8px" },
          "You have unfinished new-routine sessions — resume one instead of starting over:"),
        ...list.map((w) => el("div", { class: "row spread", style: "padding:4px 0" },
          el("span", { class: "muted", style: "font-size:12.5px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" },
            `${STAGE_TEXT[w.stage] || w.stage} · ${w.draft || "(no description)"}`),
          el("a", { class: "btn small primary", href: `#/wizard/${w.wid}` }, "resume")))));
    }).catch(() => {});

    // fill the fragment picker (default-check the common ones)
    api("/api/library").then((lib) => {
      fragBox.innerHTML = "";
      fragBox.append(el("div", { class: "muted", style: "font-size:12px;margin-bottom:3px" },
        "Standards — reusable behaviours the routine applies every run (self-management, tool safety, research). Toggle the ones that fit:"));
      const DEFAULT = new Set(["ask-policy", "global-utils", "ledger-discipline", "web-research",
        "improve-bugfix", "improve-research", "improve-features", "improve-ui", "improve-efficiency"]);
      for (const f of (lib.fragments || [])) {
        const cb = el("input", { type: "checkbox" });
        cb.checked = DEFAULT.has(f.slug); cb.dataset.slug = f.slug;
        fragBox.append(el("label", { class: "row", style: "gap:6px;font-size:12.5px;margin:2px 0" },
          cb, el("strong", { style: "min-width:130px" }, f.slug), el("span", { class: "muted" }, f.summary || "")));
      }
    }).catch(() => { fragBox.innerHTML = ""; fragBox.append(el("div", { class: "muted" }, "(couldn't load fragments)")); });
  }

  // ---- stage: clarify chat -------------------------------------------------------------------
  function stageChat(wid, snap) {
    closeSource();
    stage.innerHTML = "";
    const cancel = el("button", { class: "btn small danger", onclick: () => cancelSession(wid) }, "cancel setup");
    stage.append(el("div", { class: "row spread" },
      el("h2", {}, "Clarification — answer the questions"), cancel));
    if (snap && snap.alive === false)         // process died (e.g. across a restart) mid-conversation
      stage.append(el("div", { class: "panel", style: "border-color:var(--warn)" },
        "This session's clarification process is no longer running, so answers won't be picked up. ",
        el("button", { class: "btn small", onclick: () => cancelSession(wid) }, "cancel and start over")));
    // conversation first, then the "waiting" spinner / question input at the BOTTOM (auto-scrolled to).
    const chatBox = el("div", {});
    const thinkBox = el("div", { class: "mt" });     // "waiting on the model" while it works
    const qBox = el("div", { class: "mt" });
    stage.append(chatBox, thinkBox, qBox);
    const transcript = createTranscript(chatBox);
    const scrollDown = () => window.scrollTo(0, document.body.scrollHeight);
    let gotAny = false;

    const setThinking = (msg) => { thinkBox.innerHTML = ""; if (msg) thinkBox.append(busy(msg)); scrollDown(); };
    setThinking("Waiting for the model — reading your task and working out what to ask…");

    function showQuestion(q) {
      setThinking(null);                // a question is on screen → no longer waiting on the model
      qBox.innerHTML = "";
      const input = el("input", { type: "text", placeholder: "your answer…", style: "flex:1" });
      const send = el("button", { class: "btn primary" }, "answer");
      const submit = async () => {
        if (!input.value.trim()) return;
        try {
          await api(`/api/wizard/${encodeURIComponent(wid)}/answer`, { method: "POST",
            body: { qid: q.qid, text: input.value } });
          qBox.innerHTML = "";
          setThinking("Waiting for the model — considering your answer…");
        } catch (err) { toast(err.message); }
      };
      send.onclick = submit;
      input.onkeydown = (e) => { if (e.key === "Enter") submit(); };
      qBox.append(el("div", { class: "panel mt", style: "border-color:var(--warn)" },
        el("div", {}, `❓ ${q.question}`),
        q.options?.length ? el("div", { class: "row mt" },
          q.options.map((o) => el("button", { class: "btn small", onclick: () => { input.value = o; } }, o))) : null,
        el("div", { class: "row mt" }, input, send)));
      input.focus();
      scrollDown();
    }

    if (snap && snap.question) showQuestion(snap.question);   // seed a resumed pending question

    source = sse(`/api/wizard/${encodeURIComponent(wid)}/events`, {
      transcript: (ev) => { gotAny = true; transcript.add(ev); scrollDown(); },
      state: (s) => { gotAny = true; if (s.question) showQuestion(s.question); else setThinking("Waiting for the model…"); },
      end: () => { setThinking(null); closeSource(); stageSuggest(wid); },
      onerror: () => {
        if (gotAny) return;                    // transient mid-stream error — ignore
        closeSource();                          // couldn't attach → the session is gone
        setThinking(null);
        qBox.innerHTML = "";
        qBox.append(el("div", { class: "panel", style: "border-color:var(--warn)" },
          "This wizard session is no longer available.",
          el("div", { class: "row mt" },
            el("button", { class: "btn small primary", onclick: () => navigate("#/wizard") }, "start over"))));
      },
    });
  }

  // ---- stage: error (clarify finished with no result) ----------------------------------------
  function stageError(wid, msg) {
    closeSource();
    stage.innerHTML = "";
    stage.append(el("div", { class: "panel", style: "border-color:var(--err)" }, msg,
      el("div", { class: "row mt" },
        el("button", { class: "btn small", onclick: () => cancelSession(wid) }, "start over"))));
  }

  // ---- stage: building (the routine is scaffolding in the background) -------------------------
  function stageBuilding(wid, snap) {
    closeSource();
    clearBuilding();
    stage.innerHTML = "";
    stage.append(el("h2", {}, "Building the routine"),
      busy("Building the routine — the model is decomposing the workflow into steps tailored to your "
        + "task. This usually takes a minute or two. You can leave this page; you'll be taken to the "
        + "routine when it's ready, and the banner up top brings you back."));
    let done = false;
    const goTo = (runId, slug) => {
      if (done) return; done = true; clearBuilding(); notifyChanged();
      navigate(runId ? `#/run/${runId}` : `#/routine/${slug}`);
    };
    const failed = (msg) => {   // put the user back on the create form to retry
      if (done) return; done = true; clearBuilding(); notifyChanged();
      toast(msg, 7000); stageSuggest(wid);
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
    buildingCleanup = () => { clearInterval(poll); window.removeEventListener("rsched-bus", onBus); };
  }

  // ---- stage: suggest + finalize -------------------------------------------------------------
  async function stageSuggest(wid) {
    closeSource();
    stage.innerHTML = "";
    stage.append(busy(
      "Waiting for the model — turning the conversation into a refined instruction and "
      + "matching it to the workflow library…"));
    let data;
    try { data = await api(`/api/wizard/${encodeURIComponent(wid)}/suggest`, { method: "POST" }); }
    catch (err) { stageError(wid, `clarify run ended without a result: ${err.message}`); return; }
    const wr = data.wizard_result;
    stage.innerHTML = "";
    stage.append(el("div", { class: "row spread" },
      el("h2", {}, "Refined instruction"),
      el("button", { class: "btn small danger", onclick: () => cancelSession(wid) }, "cancel setup")),
      el("pre", { class: "doc" }, wr.refined_instruction));

    const picked = { slug: data.suggestions[0]?.slug || "" };
    const picksRow = el("div", { class: "row mt" });
    const renderPicks = () => {
      picksRow.innerHTML = "";
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
      } catch (err) { toast(err.message, 6000); }
      genBtn.disabled = false; genBtn.textContent = "✨ generate a new workflow";
      renderPicks();
    };
    stage.append(el("h2", {}, "Workflow"));
    if (data.none_fit)   // append conditionally — a bare `null` here renders as the text "null"
      stage.append(el("div", { class: "muted" }, `suggester: ${data.new_workflow_hint || "nothing fits well"}`));
    stage.append(picksRow);
    renderPicks();

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
    const createStatus = el("div", {});
    create.onclick = async () => {
      if (!picked.slug) { toast("pick a workflow"); return; }
      create.disabled = true;
      try {
        // The build runs in the BACKGROUND (decompose is a slow LLM step) — this returns at once.
        const r = await api(`/api/wizard/${encodeURIComponent(wid)}/finalize`, { method: "POST", body: {
          slug: f.slug.value.trim(), name: f.name.value.trim() || f.slug.value.trim(),
          workflow_slug: picked.slug, friendly: sched.value(), run_now: runNow.checked,
          tags: f.tags.value.split(",").map((t) => t.trim()).filter(Boolean),
          // fragments are recovered from the session meta on the backend (chosen on the draft page)
        }});
        notifyChanged();                 // the top banner now shows the build in progress
        stageBuilding(wid, { slug: r.slug });
      } catch (err) { toast(err.message, 6000); create.disabled = false; }
    };
    stage.append(el("h2", {}, "Create"),
      el("div", { class: "panel" },
        el("div", { class: "field-row" },
          el("label", { class: "field" }, el("span", {}, "slug"), f.slug),
          el("label", { class: "field" }, el("span", {}, "name"), f.name)),
        el("label", { class: "field" }, el("span", {}, "schedule"), sched.node),
        el("label", { class: "field" }, el("span", {}, "tags"), f.tags),
        el("div", { class: "muted", style: "font-size:11.5px;margin-top:-2px" },
          "suggested from the existing vocabulary — reused where they fit, new ones only for a genuinely new facet"),
        wr.notes ? el("div", { class: "muted mt" }, `wizard notes: ${wr.notes}`) : null,
        el("div", { class: "row mt" },
          el("label", { class: "row", style: "gap:4px" }, runNow, "first run immediately"),
          create),
        createStatus));
  }

  // ---- cancel: stop the backend session and return to a fresh draft --------------------------
  async function cancelSession(wid) {
    closeSource();
    try { await api(`/api/wizard/${encodeURIComponent(wid)}`, { method: "DELETE" }); } catch {}
    notifyChanged();
    navigate("#/wizard");
  }

  return () => { closeSource(); clearBuilding(); };
}

const STAGE_TEXT = { chat: "clarifying", suggest: "ready to create", building: "building the routine",
                     error: "needs attention" };
