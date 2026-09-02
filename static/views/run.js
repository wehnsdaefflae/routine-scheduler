// Run view: live transcript (resilient SSE tail with visible reconnect state), intervention
// controls, and a sub-run selector. Which sub-run you're reading — and the transcript offset —
// live in the URL (#/run/{id}?sub=N), so a deep link reopens the exact view.

import { referChip } from "/static/components/referchip.js";
import { api, apiUpload } from "/static/api.js";
import { filePicker } from "/static/components/filepicker.js";
import { questionPanel } from "/static/components/answerform.js";
import { deliberationControl } from "/static/components/deliberation.js";
import { confirmDialog, promptDialog } from "/static/components/dialog.js";
import { setQuery, remount } from "/static/router.js";
import { liveTail } from "/static/stream.js";
import { createArtifacts } from "/static/components/artifacts.js";
import { createRail } from "/static/components/rail.js";
import { createFileActivity } from "/static/components/fileactivity.js";
import { createPlanStrip } from "/static/components/planstrip.js";
import { createStateGraph } from "/static/components/stategraph.js";
import { createStopping } from "/static/components/stopping.js";
import { createTaskTree } from "/static/components/tasktree.js";
import { createTranscript } from "/static/components/transcript.js";
import { busy, chip, el, emptyState, fmtDur, fmtTokens, fmtTs, skeleton, streamStatus,
         toDate, toast } from "/static/util.js";
import { forgetField } from "/static/formpersist.js";
import { followScroll } from "/static/follow.js";
import { TERMINAL, WORKING } from "/static/states.js";
import { trace } from "/static/trace.js";

export async function render(view, runId, query = {}) {
  const [slug, ts] = runId.split(":");
  // sub ids are path-like strings ("2", or "2/1" from a search hit into a NESTED child —
  // the tab bar only has top-level tabs, so a nested link lands on its top-level subtree)
  const initialSub = query.sub != null && query.sub !== "" ? String(query.sub) : null;

  const stateChip = chip("connecting", "loading");
  const usageSpan = el("span", { class: "muted small" });
  const durSpan = el("span", { class: "muted small" });
  const modelSpan = el("span", { class: "muted small" });
  const stream = streamStatus();
  const controls = el("div", { class: "row" });
  // Home-aware breadcrumb: a conversation-home (or background) run must not link to a
  // routine page that 404s — retargeted once the run detail names its home (boot below).
  const kickerEl = el("div", { class: "kicker" }, `routine / ${slug}`);
  const titleLink = el("a", { href: `#/routine/${slug}` }, slug);
  view.append(el("div", { class: "page-head" },
    el("div", {},
      kickerEl,
      el("h1", {}, titleLink, ` · run ${fmtTs(ts)}`)),
    controls));
  view.append(el("div", { class: "runbar" }, stateChip, stream.node, usageSpan, durSpan, modelSpan));

  // The WORKING PLAN strip (D54): the run's own living decomposition (state/plan.md), shown
  // at the top so "where is this run in its own plan" is answerable at a glance. Home-agnostic
  // (keyed by run id); hides itself when the run keeps no plan. Refreshed on phase transitions.
  const planBox = el("div", {});
  view.append(planBox);
  const planStrip = createPlanStrip(planBox, { url: `/api/runs/${runId}/plan` });

  // Elapsed wall clock: start ts → last status update while live (ticking), frozen at the
  // final update once terminal.
  let lastUpdated = "";
  const tickDur = () => {
    const start = toDate(ts);
    if (!start) return;
    const end = TERMINAL.has(curState) ? toDate(lastUpdated) : new Date();
    if (end) durSpan.textContent = `⏱ ${fmtDur((end - start) / 1000)}`;
  };
  const durTimer = setInterval(tickDur, 5000);

  const questionBox = el("div", {});
  view.append(questionBox);

  // Side rail: the routine's state graph (current phase lit, updates on SSE phase
  // transitions) + its artifacts. Fixed in the right margin on wide screens (CSS), an
  // ordinary collapsible block above the transcript otherwise.
  // R341: the SHARED rail component, the same one the conversation view renders — so each
  // section is individually collapsible here too (R340), remembered per browser, instead of
  // the divergent plain-caption copy this view used to carry.
  const railHost = el("details", { class: "run-rail", open: true },
    el("summary", { class: "small" }, "state & artifacts"));
  view.append(railHost);
  const rail = createRail(railHost);
  const goalBody = rail.add("goal", el("div", {}));
  const graphBody = rail.add("state", el("div", {}));
  const treeBody = rail.add("tasks", el("div", {}));
  const filesBody = rail.add("files", el("div", {}));
  const artBody = rail.add("artifacts", el("div", {}));
  // stategraph + artifacts are HOME-scoped (routines vs conversations routes) — created
  // at boot once the run detail names its home; tree/files key off the run id (home-free).
  let stateGraph = null;
  let artifacts = null;
  const taskTree = createTaskTree(treeBody, {
    treeUrl: `/api/runs/${runId}/tree`, isLive: () => !TERMINAL.has(curState) });
  const fileActivity = createFileActivity(filesBody, { url: `/api/runs/${runId}/files` });

  // sub-run selector (main + each spawned child); hidden until there is at least one sub-run
  const subBar = el("div", { class: "subbar", hidden: true });
  view.append(subBar);

  // main transcript stays mounted (its tail keeps running); a sub-run renders into its own box
  const mainBox = el("div", { class: "mt" });
  const subBox = el("div", { class: "mt", hidden: true });
  view.append(mainBox, subBox);
  mainBox.append(skeleton(["100%", "80%", "100%"]));

  // "waiting for the model" — lives at the BOTTOM of the conversation while the run works.
  const waitingBox = el("div", { class: "mt" });
  view.append(waitingBox);

  // ONE input, ONE send — where the message goes is an EXPLICIT, visible mode, never
  // guessed from button placement: a live run injects (picked up at the next turn
  // boundary); a terminal run continues THIS run in place (rehydrated, as often as you
  // like). Queuing a message for the routine's NEXT run moved to the routine details page
  // (F233) — the end-of-run input is only ever for continuing the run you are looking at.
  // The message destination is implied by run state, not chosen: a terminal run's input
  // ALWAYS continues THIS run (converse), a live run's input injects into it (inject).
  // The old mode <select> was single-option and disabled once F233 removed the next-run
  // queue mode — a dead affordance — so it is gone (F237); the placeholder says where the
  // message goes.
  // A STABLE persist key (F215): the placeholder mutates with mode/recipe state, and
  // formpersist falls back to the placeholder as its key — so without an explicit
  // data-persist a typed draft saved under one placeholder never restores once the
  // placeholder changes. Keying it to "run-msg" makes the draft survive a refresh.
  // No inline flex: the `.composer` stylesheet rules govern its width (base: fill the row;
  // ≤860px: its own full-width line) — an inline flex would beat the media rule and re-squish
  // it inline on narrow screens, which is exactly the F238 regression this avoids.
  // ALWAYS a textarea (user order 2026-08-15, F346): a message field is multi-line prose,
  // never a one-line slot — Shift+Enter sends, Enter breaks the line (same keys as the
  // conversation composer, so the two send boxes feel like one control).
  const msgInput = el("textarea", { rows: 2, placeholder: "message…",
    "data-persist": "run-msg" });
  const sendBtn = el("button", { class: "btn primary" }, "send");
  // Attachments: the same affordance as the conversation composer (file dialog, chips,
  // paste-to-attach). Files are saved beside the run's polled inbox and auto-attached
  // by the engine — so a run message can carry screenshots/PDFs too.
  const { picker, files, clearFiles, wirePaste } = filePicker();
  wirePaste(msgInput);
  // "editable recipe" checkbox (D37, revised): sits right next to the input, OFF by
  // default. Checked, the finished run resumes as the SAME conversation — the sole
  // difference is that the continued leg may edit this routine's own recipe files
  // (main.md / stages/ / tuning.yaml) via the run-scoped unlock.
  let isTerminal = false;
  const recipeChk = el("input", { type: "checkbox", "data-nopersist": true });
  const recipeLbl = el("label", { class: "row small", hidden: true,
    style: "gap:4px;align-items:center;white-space:nowrap;color:var(--ink-2)",
    title: "when checked, the continued conversation may edit this routine's recipe files "
      + "(main.md, stages/, tuning.yaml) — it still sees this whole conversation" },
    recipeChk, el("span", {}, "editable recipe"));
  const syncPlaceholder = () => {
    msgInput.placeholder = recipeChk.checked
      ? "message… (this continuation may edit the routine's recipe files)"
      : isTerminal ? "message… (continues this run)"
      : "inject a message into the run…";
  };
  recipeChk.onchange = syncPlaceholder;
  function setModes(terminal) {
    isTerminal = terminal;
    // recipe editing targets this routine's OWN files (routine runs only) and unlocks on
    // resuming a FINISHED run.
    const recipeOk = terminal;
    recipeLbl.hidden = !recipeOk;
    if (!recipeOk) recipeChk.checked = false;
    syncPlaceholder();
  }
  setModes(false);
  const ref = referChip(msgInput, { className: "composer-ref mt" });
  const setRef = ref.setRef;
  // Own class (composer) so the mobile stylesheet can break the text input onto its own
  // full-width line (F238) instead of squishing it inline with the buttons.
  view.append(ref.node, el("div", { class: "row mt composer" }, msgInput, sendBtn, picker, recipeLbl));

  // Auto-scroll ("follow"): on by default; the user can toggle it, and scrolling up pauses it.
  let autoscroll = true;
  const followChk = el("input", { type: "checkbox", checked: true });
  followChk.onchange = () => { autoscroll = followChk.checked; if (autoscroll) scrollDown(); };
  view.append(el("label", { class: "row mt small", style: "gap:6px;color:var(--ink-2)" },
    followChk, el("span", {}, "auto-scroll to the newest message")));

  let paused = false;
  const pauseBtn = el("button", { class: "btn small" }, "⏸ pause");
  const abortBtn = el("button", { class: "btn small danger" }, "✕ abort");
  const resumeBtn = el("button", { class: "btn small", hidden: true }, "↻ resume run");
  resumeBtn.onclick = async () => {
    resumeBtn.disabled = true;
    try {
      await api(`/api/runs/${runId}/resume-run`, { method: "POST" });
      toast("resuming where it left off — reconnecting…");
      setTimeout(remount, 800);
    } catch (err) { toast(err.message, 4000, { error: true }); resumeBtn.disabled = false; }
  };
  // D69: rewind a terminal run to a chosen turn and re-open it live from there — the remedy
  // for a run that died or derailed (e.g. a context overflow) instead of losing the whole
  // conversation. Truncates the transcript through the turn (archiving the dropped tail) and
  // resumes on the same run dir.
  const rewindBtn = el("button", { class: "btn small", hidden: true,
    title: "rewind to a chosen turn and continue from there" }, "⟲ rewind");
  rewindBtn.onclick = async () => {
    const ans = await promptDialog(
      `Rewind ${runId}: keep the transcript through which turn? Everything after it is `
      + `archived and the run re-opens live from that point.`, { placeholder: "turn number" });
    if (ans == null) return;
    const turn = parseInt(ans, 10);
    if (!Number.isInteger(turn) || turn < 1) {
      toast("enter a turn number (1 or higher)", 4000, { error: true }); return;
    }
    rewindBtn.disabled = true;
    try {
      const r = await api(`/api/runs/${runId}/rewind`,
        { method: "POST", body: { turn } });
      toast(`rewound to turn ${r.kept_through_turn} — reconnecting…`);
      setTimeout(remount, 800);
    } catch (err) { toast(err.message, 4000, { error: true }); rewindBtn.disabled = false; }
  };
  controls.append(pauseBtn, abortBtn, resumeBtn, rewindBtn);

  // Live model + mid-run switch (applies at the next turn; the engine re-resolves every turn).
  const switchBox = el("details", { class: "small" },
    el("summary", { style: "cursor:pointer;color:var(--ink-2)" }, "⚙ switch model"));
  let mSelRef = null, curModel = "";   // the switch-select mirrors the LIVE model (F166)
  // F191: status.json reports the live model as an "endpoint/model" id while the switch
  // select lists catalog NAMES — resolve either form to the catalog name, otherwise the
  // assignment silently no-ops and the select shows option #1 as if it were the setting.
  let resolveModel = (m) => m;   // replaced once the catalog arrives
  const syncSel = () => {
    if (!mSelRef || !curModel) return;
    const name = resolveModel(curModel);
    if ([...mSelRef.options].some((o) => o.value === name)) mSelRef.value = name;
  };
  const setModel = (m) => {
    if (m) curModel = m;
    modelSpan.textContent = m ? `model ${m}` : "";
    syncSel();
  };
  api("/api/settings/models").then((d) => {
    const models = d.models || [];
    if (!models.length) return;
    resolveModel = (m) => (models.find((x) => x.name === m
      || (x.endpoint && x.model && `${x.endpoint}/${x.model}` === m)) || { name: m }).name;
    const mSel = el("select", { style: "width:auto;font-size:11.5px;padding:3px 6px" },
      models.map((m) => el("option", { value: m.name }, m.name)));
    mSelRef = mSel;
    syncSel();   // preselect the run's actual model (name OR endpoint/model id), not option #1
    const go = el("button", { class: "btn small primary" }, "switch");
    go.onclick = async () => {
      try {
        const r = await api(`/api/runs/${runId}/model`, { method: "POST",
          body: { model: mSel.value } });
        toast(`${r.switch} — takes effect next turn`);
      } catch (err) { toast(err.message, 4000, { error: true }); }
    };
    switchBox.append(el("div", { class: "row mt", style: "gap:5px" }, mSel, go));
  }).catch(() => {});
  controls.append(switchBox);

  // Mid-run deliberation re-level (run-scoped, like the model switch: the durable value
  // stays on the routine page). Applied at the next turn boundary via control.json.
  const delibSummary = el("summary", { style: "cursor:pointer;color:var(--ink-2)" },
    "⚙ deliberation");
  const delibBox = el("details", { class: "small" }, delibSummary);
  const delib = deliberationControl("standard", {
    onCommit: async (level) => {
      try {
        const r = await api(`/api/runs/${runId}/deliberation`, { method: "POST",
          body: { level } });
        toast(`${r.switch} — takes effect next turn (this run)`);
        delibSummary.textContent = `⚙ deliberation: ${level}`;
      } catch (err) { toast(err.message, 4000, { error: true }); }
    },
  });
  delibBox.append(el("div", { class: "mt" }, delib.node));
  controls.append(delibBox);

  // ---- transcript sources: main run = resilient tail; a sub-run = paged fetch + poll ----------
  let curState = "";
  const subs = new Map();          // n -> label
  let viewingSub = null;           // null = main, else sub-run number
  let tail = null;                 // the always-on main tail (state + main transcript)
  let subPoll = null, subOffset = 0, subTranscript = null;

  const scrollDown = () => { if (autoscroll) window.scrollTo(0, document.body.scrollHeight); };
  // What the run is ACTUALLY doing (F170): the engine emits assistant_action when a
  // turn's action starts executing and observation when it lands — between the two the
  // run waits on the ACTION (a util, a sub-run, a file op), not on the model.
  let pendingAction = null;
  const waitingLabel = () => {
    const p = pendingAction;
    if (!p || !p.kind) return "waiting for the model…";
    if (p.kind === "util") return p.name ? `running util ${p.name}…` : "running a util…";
    if (p.kind === "llm") return "running an LLM subcall…";
    if (["spawn", "subtask", "detach", "wait"].includes(p.kind)) return "waiting on sub-runs…";
    if (p.kind === "ask_user") return "waiting for your answer…";
    return `executing ${p.kind}…`;
  };
  const setWaiting = (active) => {   // shown only for the main run (a sub-run is polled, not live)
    waitingBox.replaceChildren();
    if (active && viewingSub == null) waitingBox.append(busy(waitingLabel()));
  };

  function stopSubPoll() { if (subPoll) { clearInterval(subPoll); subPoll = null; } }

  function renderSubBar() {
    if (!subs.size) { subBar.hidden = true; subBar.replaceChildren(); return; }
    subBar.hidden = false;
    subBar.replaceChildren(el("span", { class: "faint small" }, "transcript:"));
    const tab = (n, text) => el("button",
      { class: `btn small ${viewingSub === n ? "primary" : ""}`, onclick: () => selectSub(n) }, text);
    subBar.append(tab(null, "main"));
    for (const [n, label] of [...subs.entries()].sort((a, b) => a[0] - b[0]))
      subBar.append(tab(n, `#${n} ${label}`));
  }

  function addSubTab(n, label) {
    if (!subs.has(n) || (label && subs.get(n) !== label)) {
      subs.set(n, label || subs.get(n) || `sub ${n}`);
      renderSubBar();
    }
  }

  function selectSub(n) {
    if (viewingSub === n) return;
    viewingSub = n;
    setQuery({ sub: n == null ? "" : String(n), offset: "" });   // offset is a load-time deep link only
    stopSubPoll();
    renderSubBar();
    setWaiting(WORKING.has(curState));   // main only; cleared while reading a sub
    mainBox.hidden = n != null;          // the main tail keeps running underneath
    subBox.hidden = n == null;
    if (n != null) mountSubPolling(n, 0);
  }

  function mountSubPolling(n, startOffset) {
    stopSubPoll();
    subBox.replaceChildren();
    subTranscript = createTranscript(subBox, {
      loadSub: (m, o) => api(`/api/runs/${runId}/transcript?sub=${n}/${m}&offset=${o}`),
      isLive: () => !TERMINAL.has(curState),
      onRefer: setRef,
      fileUrl: (rel) => `/api/runs/${runId}/file?path=${encodeURIComponent(rel)}`,
    });
    subOffset = startOffset || 0;
    const pull = async () => {
      try {
        const { events, offset } = await api(`/api/runs/${runId}/transcript?sub=${n}&offset=${subOffset}`);
        subOffset = offset;
        for (const ev of events) subTranscript.add(ev);
        if (events.length) scrollDown();
      } catch { /* transient — keep polling */ }
    };
    pull();
    subPoll = setInterval(() => { TERMINAL.has(curState) ? stopSubPoll() : pull(); }, 3000);
  }

  // ---- state + controls -----------------------------------------------------------------------
  function setState(state) {
    curState = state;
    stateChip.textContent = state;
    stateChip.className = `chip ${state}`;
    const terminal = TERMINAL.has(state);
    pauseBtn.disabled = abortBtn.disabled = terminal;
    pauseBtn.hidden = abortBtn.hidden = terminal;   // controls for a live run
    resumeBtn.hidden = !terminal;                   // resume only a terminal run
    rewindBtn.hidden = !terminal;                   // D69: rewind only a terminal run
    if (terminal) rewindBtn.disabled = false;
    switchBox.hidden = terminal;                    // no mid-run switch once the run has ended
    delibBox.hidden = terminal;                     // deliberation re-level is mid-run only
    setModes(terminal);
    tickDur();
    if (state === "paused") { paused = true; pauseBtn.textContent = "▶ resume"; }
    else if (paused && state !== "paused") { paused = false; pauseBtn.textContent = "⏸ pause"; }
    setWaiting(WORKING.has(state));                 // the model is working
    scrollDown();
  }

  let shownQid = null;
  function showQuestion(q) {
    // Diagnostic (F93): trace only real transitions of the shown question (SSE state events
    // fire often) — captures whether/when the run page rendered a given clarify question.
    const qid = q ? q.qid : null;
    if (qid !== shownQid) { trace("run-question", qid || "none", curState); shownQid = qid; }
    questionPanel(questionBox, q);
  }

  pauseBtn.onclick = async () => {
    try { await api(`/api/runs/${runId}/${paused ? "resume" : "pause"}`, { method: "POST" }); }
    catch (err) { toast(err.message, 4000, { error: true }); }
  };
  abortBtn.onclick = async () => {
    if (!(await confirmDialog(`Abort ${runId}?`, { confirmLabel: "abort" }))) return;
    try { await api(`/api/runs/${runId}/abort`, { method: "POST" }); }
    catch (err) { toast(err.message, 4000, { error: true }); }
  };
  const doSend = async () => {
    if (!msgInput.value.trim()) return;
    const mode = isTerminal ? "converse" : "inject";
    const text = ref.pending
      ? `> re ${ref.pending.label}: ${ref.pending.snippet}\n\n${msgInput.value}`
      : msgInput.value;
    sendBtn.disabled = true;
    try {
      const fd = new FormData();
      fd.append("text", text);
      for (const f of files()) fd.append("files", f);
      if (mode === "converse") {
        if (recipeChk.checked) fd.append("recipe_edit", "1");
        await apiUpload(`/api/runs/${runId}/converse`, fd);
        msgInput.value = "";     // clear on submit (F215) — don't leave sent text on screen
        clearFiles();
        forgetField(msgInput);   // delivered — must not refill after the reload below
        toast(recipeChk.checked
          ? "message delivered — the conversation continues with an editable recipe…"
          : "message delivered — waking the run to continue the conversation…");
        setTimeout(remount, 800);   // reattach the tail to the now-live run
        return;                  // keep the button disabled until the remount lands
      }
      const r = await apiUpload(`/api/runs/${runId}/inject`, fd);
      toast(r.delivery === "mid-run" ? "injected — picked up at the next turn" : "queued for the next run");
      msgInput.value = "";
      clearFiles();
      setRef(null);
      forgetField(msgInput);   // sent — the draft must not refill on reload
    } catch (err) { toast(err.message, 4000, { error: true }); }
    sendBtn.disabled = false;
  };
  sendBtn.onclick = doSend;
  msgInput.onkeydown = (e) => {
    if (e.key === "Enter" && e.shiftKey) { e.preventDefault(); doSend(); }
  };

  // ---- boot -----------------------------------------------------------------------------------
  let detail;
  try { detail = await api(`/api/runs/${runId}`); }
  catch (err) {
    mainBox.replaceChildren(emptyState("✕", "Run not found",
      `${err.message} — it may have been pruned by retention.`));
    return;
  }
  const home = detail.home || "routine";
  if (home === "conversation") {
    kickerEl.textContent = `conversation / ${slug}`;
    titleLink.href = `#/conversations/${slug}`;
    stateGraph = createStateGraph(graphBody, {
      graphUrl: `/api/conversations/${slug}/stategraph`,
      statsUrl: `/api/runs/${runId}/phases` });
    artifacts = createArtifacts(artBody, { slug, base: "conversations" });
    createStopping(goalBody, { url: `/api/conversations/${slug}/stopping` });
  } else if (home === "background") {
    // a detached task has no page/routes of its own — results deliver to the owner
    kickerEl.textContent = `background task / ${slug}`;
    titleLink.removeAttribute("href");
    graphBody.append(el("div", { class: "faint small" },
      "detached background task — its result is delivered to the owning conversation"));
    // a detached task's bounds are its OWNER's; it has no goal surface of its own
    rail.toggle("goal", false);
  } else {
    stateGraph = createStateGraph(graphBody, {
      graphUrl: `/api/routines/${slug}/stategraph`,
      statsUrl: `/api/runs/${runId}/phases` });
    artifacts = createArtifacts(artBody, { slug, base: "routines" });
    // showStage: a per-stage condition is a ROUTINE concept — a conversation has no stages
    createStopping(goalBody, { url: `/api/routines/${slug}/stopping`, showStage: true });
  }
  mainBox.replaceChildren();
  const transcript = createTranscript(mainBox, {
    // deferred questions become answerable right in the conversation…
    answer: async (qid, text, decision) =>
      api(`/api/questions/${qid}/answer`, { method: "POST", body: decision ? { decision } : { text } }),
    // …and subrun lines unfold into the child's own conversation, in place.
    loadSub: (n, o) => api(`/api/runs/${runId}/transcript?sub=${n}&offset=${o}`),
    isLive: () => !TERMINAL.has(curState),
    onRefer: setRef,
    // message attachments render inline: the run file route serves attachments/ rels
    fileUrl: (rel) => `/api/runs/${runId}/file?path=${encodeURIComponent(rel)}`,
  });

  // Question state stays in sync everywhere: an answer given on the Decisions page (or in
  // another tab) closes the inline form here via the bus; at boot, questions this run
  // asked that were settled later (or consumed by a later run) render as settled.
  const onBus = (e) => {
    const ev = e.detail || {};
    if (ev.event === "question_answered") transcript.closeQuestion(ev.qid,
      "✅ answered (queued for the next run)");
  };
  window.addEventListener("rsched-bus", onBus);
  const syncQuestions = async () => {
    try {
      const t0 = Date.now();
      const qs = await api("/api/questions");
      transcript.reconcileQuestions(
        new Set(qs.filter((q) => q.routine === slug && !q.answered).map((q) => q.qid)), t0);
    } catch { /* cosmetic — forms just stay open */ }
  };
  setTimeout(syncQuestions, 1500);   // after the initial transcript page has rendered

  setState(detail.state);
  usageSpan.textContent = fmtTokens(detail.usage);
  lastUpdated = detail.updated || "";
  tickDur();
  setModel(detail.model);
  if (detail.deliberation) {
    delib.set(detail.deliberation);
    delibSummary.textContent = `⚙ deliberation: ${detail.deliberation}`;
  }
  showQuestion(detail.question);
  for (const n of detail.subruns || []) subs.set(n, `sub ${n}`);
  const subIds = (detail.subruns || []).map(String);
  const wantedSub = initialSub == null ? null
    : subIds.includes(initialSub) ? initialSub
    : subIds.includes(initialSub.split("/")[0]) ? initialSub.split("/")[0] : null;
  viewingSub = wantedSub == null ? null : Number(wantedSub);
  renderSubBar();
  mainBox.hidden = viewingSub != null;
  subBox.hidden = viewingSub == null;

  // The main tail runs for the whole life of the view: transcript + state, reconnecting with
  // backoff and resuming from its last confirmed offset when the stream drops.
  tail = liveTail({
    page: (o) => `/api/runs/${runId}/transcript?offset=${o}`,
    events: (o) => `/api/runs/${runId}/events?offset=${o}`,
    offset: 0,
    onEvent: (ev) => {
      if (ev.type === "assistant_action") {
        pendingAction = ev.payload || null; setWaiting(WORKING.has(curState));
      } else if (ev.type === "observation" || ev.type === "finish") {
        pendingAction = null; setWaiting(WORKING.has(curState));
      }
      if (ev.type === "subrun_start") { addSubTab(ev.payload.n, ev.payload.label); taskTree.refresh(); }
      if (ev.type === "subrun_end") taskTree.refresh();
      // a deliverable landed — the rail refreshes without waiting for run end
      if (ev.type === "observation" && !ev.payload?.error
          && (ev.payload?.kind === "write_file" || ev.payload?.kind === "edit_file")
          && String(ev.payload?.path || "").includes("artifacts/")) artifacts?.refresh();
      if (ev.type === "observation" && ["read_file", "view_image", "write_file", "edit_file"]
          .includes(ev.payload?.kind)) fileActivity.poke();
      transcript.add(ev);
      if (viewingSub == null) scrollDown();
    },
    onState: (s) => {
      if (s.updated) lastUpdated = s.updated;
      setState(s.state);
      stateGraph?.setPhase(s.phase);
      planStrip.refresh();   // the plan is a living doc — track the run's edits as it advances
      if (s.usage) usageSpan.textContent = fmtTokens(s.usage);
      if (s.model) setModel(s.model);
      showQuestion(s.question);
      if (TERMINAL.has(s.state)) { artifacts?.refresh(); taskTree.refresh(); fileActivity.refresh(); }
    },
    onStatus: (s) => stream.set(s),
    onGone: () => stream.set("ended"),
  });
  if (viewingSub != null) mountSubPolling(viewingSub, 0);

  // Manual scroll pauses following; scrolling back to the bottom resumes it (follow.js —
  // only an upward move pauses). The checkbox mirrors the live follow state.
  const stopFollow = followScroll({ margin: 60,
    pause: () => { if (followChk.checked) { followChk.checked = false; autoscroll = false; } },
    resume: () => { if (!followChk.checked) { followChk.checked = true; autoscroll = true; } },
  });

  return () => { if (tail) tail.stop(); stopSubPoll(); clearInterval(durTimer);
                 artifacts?.destroy();
                 planStrip.destroy();
                 stopFollow();
                 window.removeEventListener("rsched-bus", onBus); };
}
