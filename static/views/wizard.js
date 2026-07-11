// New-routine wizard: draft → clarify chat (a real engine run) → workflow pick → finalize.
//
// Fully URL-driven and resumable: a live session lives at #/wizard/{wid}. Navigating away or
// reloading mid-creation never loses your place — the stage is reconstructed from the backend
// (/api/wizard/{wid}), which derives it from what the clarify run has produced on disk. The
// draft stage (#/wizard) also lists any in-flight sessions so you can resume instead of forking.
// The chat tails the clarify run through stream.js liveTail, so a dropped stream reconnects
// (visibly) and resumes from its last offset. The suggest/finalize stages live in
// wizard-create.js.

import { api } from "/static/api.js";
import { navigate } from "/static/router.js";
import { liveTail } from "/static/stream.js";
import { createTranscript } from "/static/components/transcript.js";
import { busy, el, grantsSummary, skeleton, streamStatus, toast } from "/static/util.js";
import { stageSuggest, stageBuilding } from "/static/views/wizard-create.js";

// Tell app.js a session started / was canceled / finalized so the setup banner updates at once.
const notifyChanged = () => window.dispatchEvent(new CustomEvent("rsched-wizard-changed"));

const STAGE_TEXT = { chat: "clarifying", suggest: "ready to create", building: "building the routine",
                     error: "needs attention" };

export async function render(view, resumeWid) {
  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("div", { class: "kicker" }, "console / new routine"),
      el("h1", {}, "New routine"))));
  const stage = el("div", {});
  view.append(stage);
  stage.append(skeleton(["100%", "60%"]));

  const st = await api("/api/status").catch(() => ({}));
  if (st.llm_ready === false) {
    stage.replaceChildren(el("div", { class: "panel warn" },
      el("strong", {}, "No model connected"),
      el("div", { class: "muted mt prose" },
        "Creating a routine runs a clarification through your LLM. Set the ",
        el("code", {}, "system model"), " in ", el("a", { href: "#/settings" }, "Settings"), " first.")));
    return;
  }

  let tail = null;
  const closeTail = () => { if (tail) { tail.stop(); tail = null; } };
  let buildingCleanup = null;   // stops the background-build poll + bus listener
  const clearBuilding = () => { if (buildingCleanup) { buildingCleanup(); buildingCleanup = null; } };

  // Shared context for the create stages (wizard-create.js).
  const ctx = {
    stage, notifyChanged, closeTail, clearBuilding,
    setBuildingCleanup: (fn) => { buildingCleanup = fn; },
    cancelSession, stageError,
  };

  if (resumeWid) resumeSession(resumeWid);   // #/wizard/{wid} → reconstruct the live session
  else stageDraft();

  // ---- resume: fetch the session snapshot and jump to the right stage -----------------------
  async function resumeSession(wid) {
    stage.replaceChildren(busy("Reconnecting to your setup session…"));
    let snap;
    try { snap = await api(`/api/wizard/${encodeURIComponent(wid)}`); }
    catch {
      stage.replaceChildren(el("div", { class: "panel warn" },
        "This setup session is no longer available.",
        el("div", { class: "row mt" },
          el("button", { class: "btn small primary", onclick: () => navigate("#/wizard") }, "start over"))));
      return;
    }
    if (snap.stage === "building") stageBuilding(ctx, wid, snap);
    else if (snap.stage === "done") navigate(snap.run_id ? `#/run/${snap.run_id}` : `#/routine/${snap.slug}`);
    else if (snap.stage === "suggest") stageSuggest(ctx, wid);
    else if (snap.stage === "error")
      stageError(wid, snap.error ? `couldn't build the routine: ${snap.error}` : "The clarification run ended without a result.");
    else stageChat(wid, snap);
  }

  // ---- stage: draft --------------------------------------------------------------------------
  function stageDraft() {
    closeTail();
    stage.replaceChildren();
    const resumeBox = el("div", {});     // filled with any in-flight sessions to resume
    const ta = el("textarea", { class: "code", style: "min-height:160px",
      placeholder: "Describe the TASK the routine should do, in your own words — not when it runs.\n\ne.g. Collect new AI-agent papers from arxiv and keep a reading list with one-line takes." });
    const fragBox = el("div", { class: "mt" }, el("div", { class: "muted small" }, "Standards — loading…"));
    const chosen = () => Array.from(fragBox.querySelectorAll("input:checked")).map((c) => c.dataset.slug);
    const go = el("button", { class: "btn primary" }, "start clarification");
    go.onclick = async () => {
      if (!ta.value.trim()) return;
      go.disabled = true;
      try {
        const r = await api("/api/wizard/start", { method: "POST", body: { draft: ta.value, fragments: chosen() } });
        notifyChanged();
        navigate(`#/wizard/${r.wid}`);       // the session's URL is now the source of truth
      } catch (err) { toast(err.message, 4000, { error: true }); go.disabled = false; }
    };
    stage.append(resumeBox, el("div", { class: "panel" },
      el("div", { class: "muted prose", style: "margin-bottom:8px" },
        "Describe the task in your own words. The wizard asks a few clarifying questions, then builds the ",
        "routine. Below, choose its standards — reusable habits it follows every run, and the ONLY way it ",
        "gains permissions: a fragment's grants unlock gated capabilities like writing utils or messaging ",
        "you on Discord. You can change these, its schedule and its models afterwards."),
      ta, fragBox, el("div", { class: "row mt" }, go)));

    // Surface any in-flight sessions so the user resumes instead of starting a second one.
    api("/api/wizard").then((list) => {
      if (!Array.isArray(list) || !list.length) return;
      resumeBox.append(el("div", { class: "panel warn", style: "margin-bottom:14px" },
        el("strong", {}, "Setup already in progress"),
        el("div", { class: "muted small", style: "margin:4px 0 8px" },
          "You have unfinished new-routine sessions — resume one instead of starting over:"),
        ...list.map((w) => el("div", { class: "row spread", style: "padding:4px 0" },
          el("span", { class: "muted small", style: "min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" },
            `${STAGE_TEXT[w.stage] || w.stage} · ${w.draft || "(no description)"}`),
          el("a", { class: "btn small primary", href: `#/wizard/${w.wid}` }, "resume")))));
    }).catch(() => {});

    // Fill the fragment picker; the defaults come from the backend (config.DEFAULT_FRAGMENTS
    // via /api/library) so the UI never drifts from what a scaffold would actually apply.
    api("/api/library").then((lib) => {
      fragBox.replaceChildren(el("div", { class: "muted small", style: "margin-bottom:3px" },
        "Standards & capabilities — each fragment is a behaviour the routine applies every run; ",
        "ones marked ▸ also GRANT it a gated capability (util authoring, a reserved util). Toggle the ones that fit:"));
      const defaults = new Set(lib.default_fragments || []);
      for (const f of (lib.fragments || [])) {
        const cb = el("input", { type: "checkbox" });
        cb.checked = defaults.has(f.slug);
        cb.dataset.slug = f.slug;
        const grants = grantsSummary(f.grants);
        fragBox.append(el("label", { class: "row", style: "gap:6px;font-size:12px;margin:2px 0" },
          cb, el("strong", { style: "min-width:130px" }, f.slug),
          el("span", { class: "muted prose" }, f.summary || "",
            grants ? el("span", { style: "color:var(--warn)" }, ` ▸ ${grants}`) : "")));
      }
    }).catch(() => {
      fragBox.replaceChildren(el("div", { class: "muted" }, "(couldn't load fragments)"));
    });
  }

  // ---- stage: clarify chat -------------------------------------------------------------------
  function stageChat(wid, snap) {
    closeTail();
    stage.replaceChildren();
    const cancel = el("button", { class: "btn small danger", onclick: () => cancelSession(wid) }, "cancel setup");
    const stream = streamStatus();
    stage.append(el("div", { class: "row spread" },
      el("h2", {}, "Clarification — answer the questions"),
      el("div", { class: "row" }, stream.node, cancel)));
    if (snap && snap.alive === false)         // process died (e.g. across a restart) mid-conversation
      stage.append(el("div", { class: "panel warn" },
        "This session's clarification process is no longer running, so answers won't be picked up. ",
        el("button", { class: "btn small", onclick: () => cancelSession(wid) }, "cancel and start over")));
    // conversation first, then the "waiting" spinner / question input at the BOTTOM (auto-scrolled to).
    const chatBox = el("div", { class: "chatwrap" });
    const thinkBox = el("div", { class: "mt" });     // "waiting on the model" while it works
    const qBox = el("div", { class: "mt" });
    stage.append(chatBox, thinkBox, qBox);
    const transcript = createTranscript(chatBox);
    const scrollDown = () => window.scrollTo(0, document.body.scrollHeight);

    const setThinking = (msg) => { thinkBox.replaceChildren(); if (msg) thinkBox.append(busy(msg)); scrollDown(); };
    setThinking("Waiting for the model — reading your task and working out what to ask…");

    function showQuestion(q) {
      setThinking(null);                // a question is on screen → no longer waiting on the model
      qBox.replaceChildren();
      const input = el("input", { type: "text", placeholder: "your answer…", style: "flex:1" });
      const send = el("button", { class: "btn primary" }, "answer");
      // A dialog reply: the model responds and re-asks — for when you need to ask back
      // (or think out loud) before you can actually answer.
      const discuss = el("button", { class: "btn",
        title: "send as a follow-up question / thought — the model replies and the question stays open" },
        "ask back");
      const submit = async (intermediate) => {
        if (!input.value.trim()) return;
        try {
          await api(`/api/wizard/${encodeURIComponent(wid)}/answer`, { method: "POST",
            body: { qid: q.qid, text: input.value, intermediate } });
          qBox.replaceChildren();
          setThinking(intermediate ? "Waiting for the model — replying in the dialog…"
                                   : "Waiting for the model — considering your answer…");
        } catch (err) { toast(err.message, 4000, { error: true }); }
      };
      send.onclick = () => submit(false);
      discuss.onclick = () => submit(true);
      input.onkeydown = (e) => { if (e.key === "Enter") submit(false); };
      qBox.append(el("div", { class: "panel warn mt" },
        el("div", { class: "prose" }, `❓ ${q.question}`),
        q.options?.length ? el("div", { class: "row mt" },
          q.options.map((o) => el("button", { class: "btn small", onclick: () => { input.value = o; } }, o))) : null,
        el("div", { class: "row mt" }, input, send, discuss)));
      input.focus();
      scrollDown();
    }

    if (snap && snap.question) showQuestion(snap.question);   // seed a resumed pending question

    tail = liveTail({
      page: (o) => `/api/wizard/${encodeURIComponent(wid)}/transcript?offset=${o}`,
      events: (o) => `/api/wizard/${encodeURIComponent(wid)}/events?offset=${o}`,
      onEvent: (ev) => { transcript.add(ev); scrollDown(); },
      onState: (s) => { if (s.question) showQuestion(s.question); else setThinking("Waiting for the model…"); },
      onStatus: (s) => stream.set(s),
      onEnd: () => { setThinking(null); closeTail(); stageSuggest(ctx, wid); },
      onGone: () => {              // 404: the session was archived/canceled underneath us
        setThinking(null);
        qBox.replaceChildren(el("div", { class: "panel warn" },
          "This wizard session is no longer available.",
          el("div", { class: "row mt" },
            el("button", { class: "btn small primary", onclick: () => navigate("#/wizard") }, "start over"))));
      },
    });
  }

  // ---- stage: error (clarify finished with no result) ----------------------------------------
  function stageError(wid, msg) {
    closeTail();
    stage.replaceChildren(el("div", { class: "panel err" }, msg,
      el("div", { class: "row mt" },
        el("button", { class: "btn small", onclick: () => cancelSession(wid) }, "start over"))));
  }

  // ---- cancel: stop the backend session and return to a fresh draft --------------------------
  async function cancelSession(wid) {
    closeTail();
    try { await api(`/api/wizard/${encodeURIComponent(wid)}`, { method: "DELETE" }); } catch { /* already gone */ }
    notifyChanged();
    navigate("#/wizard");
  }

  return () => { closeTail(); clearBuilding(); };
}
