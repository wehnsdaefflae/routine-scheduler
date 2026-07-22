// Run view: live transcript (resilient SSE tail with visible reconnect state), intervention
// controls, and a sub-run selector. Which sub-run you're reading — and the transcript offset —
// live in the URL (#/run/{id}?sub=N&offset=M), so a deep link reopens the exact view.

import { api } from "/static/api.js";
import { answerForm } from "/static/components/answerform.js";
import { deliberationControl } from "/static/components/deliberation.js";
import { confirmDialog } from "/static/components/dialog.js";
import { mdInline } from "/static/md.js";
import { setQuery } from "/static/router.js";
import { liveTail } from "/static/stream.js";
import { createArtifacts } from "/static/components/artifacts.js";
import { createFileActivity } from "/static/components/fileactivity.js";
import { createSetupPanel } from "/static/components/setuppanel.js";
import { createStateGraph } from "/static/components/stategraph.js";
import { createTaskTree } from "/static/components/tasktree.js";
import { createTranscript } from "/static/components/transcript.js";
import { busy, chip, el, emptyState, fmtDur, fmtTokens, fmtTs, skeleton, streamStatus,
         toDate, toast, when } from "/static/util.js";
import { forgetField } from "/static/formpersist.js";
import { followScroll } from "/static/follow.js";
import { TERMINAL, WORKING } from "/static/states.js";
import { trace } from "/static/trace.js";

export async function render(view, runId, query = {}) {
  const [slug, ts] = runId.split(":");
  const initialSub = query.sub != null && query.sub !== "" ? Number(query.sub) : null;
  const initialOffset = Number(query.offset) || 0;

  const stateChip = chip("connecting", "loading");
  const usageSpan = el("span", { class: "muted small" });
  const durSpan = el("span", { class: "muted small" });
  const modelSpan = el("span", { class: "muted small" });
  const stream = streamStatus();
  const controls = el("div", { class: "row" });
  view.append(el("div", { class: "page-head" },
    el("div", {},
      el("div", { class: "kicker" }, `routine / ${slug}`),
      el("h1", {}, el("a", { href: `#/routine/${slug}` }, slug), ` · run ${fmtTs(ts)}`)),
    controls));
  view.append(el("div", { class: "runbar" }, stateChip, stream.node, usageSpan, durSpan, modelSpan));

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

  // New-routine setup: a clarification run with a live session behind it gets the setup
  // panel (components/setuppanel.js) — chat frame while live, the create form once done.
  const setupBox = el("div", {});
  view.append(setupBox);
  let setup = null;

  // Side rail: the routine's state graph (current phase lit, updates on SSE phase
  // transitions) + its artifacts. Fixed in the right margin on wide screens (CSS), an
  // ordinary collapsible block above the transcript otherwise.
  const graphBody = el("div", {});
  const treeBody = el("div", {});
  const filesBody = el("div", {});
  const artBody = el("div", {});
  view.append(el("details", { class: "run-rail", open: true },
    el("summary", { class: "small" }, "state & artifacts"),
    el("div", { class: "rail-cap" }, "state"), graphBody,
    el("div", { class: "rail-cap" }, "tasks"), treeBody,
    el("div", { class: "rail-cap" }, "files"), filesBody,
    el("div", { class: "rail-cap" }, "artifacts"), artBody));
  const stateGraph = createStateGraph(graphBody, {
    graphUrl: `/api/routines/${slug}/stategraph`,
    statsUrl: `/api/runs/${runId}/phases` });
  const taskTree = createTaskTree(treeBody, {
    treeUrl: `/api/runs/${runId}/tree`, isLive: () => !TERMINAL.has(curState) });
  const fileActivity = createFileActivity(filesBody, { url: `/api/runs/${runId}/files` });
  const artifacts = createArtifacts(artBody, { slug, base: "routines" });

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
  // boundary); a terminal run either continues THIS run in place (rehydrated, as often
  // as you like) or queues the message for the routine's next run.
  const MODES = {
    inject: "→ live run",
    converse: "→ continue this run",
    queue: "→ queue for next run",
    revise: "→ revise this routine's recipe",
  };
  const modeSel = el("select", { class: "small", "data-nopersist": true,
    title: "where this message goes" });
  const msgInput = el("input", { type: "text", placeholder: "message…", style: "flex:1" });
  const sendBtn = el("button", { class: "btn primary" }, "send");
  function setModes(terminal) {
    // "revise" edits this routine's OWN recipe (routine runs only; never the protected
    // clarification template, which the /revise endpoint would run its recipe against).
    const reviseOk = terminal && slug !== "clarification";
    const keys = terminal ? (reviseOk ? ["converse", "queue", "revise"] : ["converse", "queue"])
      : ["inject"];
    if (![...modeSel.options].some((o) => keys.includes(o.value)) || modeSel.options.length !== keys.length) {
      modeSel.replaceChildren(...keys.map((k) => el("option", { value: k }, MODES[k])));
    }
    modeSel.disabled = keys.length === 1;
    msgInput.placeholder = terminal
      ? "message… (the mode selector says where it goes)"
      : "inject a message into the run…";
  }
  setModes(false);
  // "refer to" (the messenger reply analog): a hover ↩ on any transcript message primes
  // this chip; the send prepends the quoted reference line to the message text.
  let pendingRef = null;
  const refText = el("span", { class: "ref-text" });
  const refClear = el("button", { class: "btn small ghost", title: "drop the reference" }, "✕");
  const refBar = el("div", { class: "composer-ref mt", hidden: true }, "↩ ", refText, refClear);
  const setRef = (r) => {
    pendingRef = r;
    refBar.hidden = !r;
    if (r) { refText.textContent = `${r.label}: ${r.snippet}`; msgInput.focus(); }
  };
  refClear.onclick = () => setRef(null);
  view.append(refBar, el("div", { class: "row mt" }, modeSel, msgInput, sendBtn));

  // Auto-scroll ("follow"): on by default; the user can toggle it, and scrolling up pauses it.
  let autoscroll = true;
  const followChk = el("input", { type: "checkbox", checked: true });
  followChk.onchange = () => { autoscroll = followChk.checked; if (autoscroll) scrollDown(); };
  view.append(el("label", { class: "row mt small", style: "gap:6px;color:var(--muted)" },
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
      setTimeout(() => location.reload(), 800);
    } catch (err) { toast(err.message, 4000, { error: true }); resumeBtn.disabled = false; }
  };
  controls.append(pauseBtn, abortBtn, resumeBtn);

  // Live model + mid-run switch (applies at the next turn; the engine re-resolves every turn).
  const switchBox = el("details", { class: "small" },
    el("summary", { style: "cursor:pointer;color:var(--muted)" }, "⚙ switch model"));
  const setModel = (m) => { modelSpan.textContent = m ? `model ${m}` : ""; };
  api("/api/settings/models").then((d) => {
    const models = d.models || [];
    if (!models.length) return;
    const mSel = el("select", { style: "width:auto;font-size:11.5px;padding:3px 6px" },
      models.map((m) => el("option", {}, m.name)));
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
  const delibSummary = el("summary", { style: "cursor:pointer;color:var(--muted)" },
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
  const setWaiting = (active) => {   // shown only for the main run (a sub-run is polled, not live)
    waitingBox.replaceChildren();
    if (active && viewingSub == null) waitingBox.append(busy("waiting for the model…"));
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
    switchBox.hidden = terminal;                    // no mid-run switch once the run has ended
    delibBox.hidden = terminal;                     // deliberation re-level is mid-run only
    setModes(terminal);
    if (setup) setup.onRunState(state);
    tickDur();
    if (state === "paused") { paused = true; pauseBtn.textContent = "▶ resume"; }
    else if (paused && state !== "paused") { paused = false; pauseBtn.textContent = "⏸ pause"; }
    setWaiting(WORKING.has(state));                 // the model is working
    scrollDown();
  }

  let shownQid = null;
  function showQuestion(q) {
    questionBox.replaceChildren();
    // Diagnostic (F93): trace only real transitions of the shown question (SSE state events
    // fire often) — captures whether/when the run page rendered a given clarify question.
    const qid = q ? q.qid : null;
    if (qid !== shownQid) { trace("run-question", qid || "none", curState); shownQid = qid; }
    if (!q) return;
    const form = answerForm(q, {
      submitText: (text, intermediate) => api(`/api/questions/${q.qid}/answer`,
        { method: "POST", body: { text, intermediate } }),
      askBack: true,
      toastText: (i) => (i ? "sent — the model will reply and re-ask" : "answer sent"),
      onSuccess: () => questionBox.replaceChildren(),
    });
    questionBox.append(el("div", { class: "panel warn mt" },
      el("div", { class: "prose" },
        "❓ ", q.type === "util-approval" ? el("strong", {}, "[util approval] ") : null,
        mdInline(q.question)),
      q.expires ? el("div", { class: "faint small" },
        "the run continues without you ", when(q.expires, { mode: "rel" }),
        " — also answerable on the Decisions page",
        q.mirrored ? " and on Discord" : "") : null,
      form.node));
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
    const mode = modeSel.value;
    const text = pendingRef
      ? `> re ${pendingRef.label}: ${pendingRef.snippet}\n\n${msgInput.value}`
      : msgInput.value;
    sendBtn.disabled = true;
    try {
      if (mode === "converse") {
        await api(`/api/runs/${runId}/converse`, { method: "POST", body: { text } });
        forgetField(msgInput);   // delivered — must not refill after the reload below
        toast("message delivered — waking the run to continue the conversation…");
        setTimeout(() => location.reload(), 800);   // reattach the tail to the now-live run
        return;                  // keep the button disabled until the reload lands
      }
      if (mode === "revise") {
        await api(`/api/runs/${runId}/revise`, { method: "POST", body: { text } });
        forgetField(msgInput);
        toast("revising the recipe — the run resumes to apply your change, then commits it…");
        setTimeout(() => location.reload(), 800);   // reattach to the now-live revise run
        return;
      }
      const r = await api(`/api/runs/${runId}/inject`, { method: "POST", body: { text } });
      toast(r.delivery === "mid-run" ? "injected — picked up at the next turn" : "queued for the next run");
      msgInput.value = "";
      setRef(null);
      forgetField(msgInput);   // sent — the draft must not refill on reload
    } catch (err) { toast(err.message, 4000, { error: true }); }
    sendBtn.disabled = false;
  };
  sendBtn.onclick = doSend;
  msgInput.onkeydown = (e) => { if (e.key === "Enter") doSend(); };

  // ---- boot -----------------------------------------------------------------------------------
  let detail;
  try { detail = await api(`/api/runs/${runId}`); }
  catch (err) {
    mainBox.replaceChildren(emptyState("✕", "Run not found",
      `${err.message} — it may have been pruned by retention.`));
    return;
  }
  if (slug === "clarification") setup = await createSetupPanel(setupBox, { ts });
  mainBox.replaceChildren();
  const transcript = createTranscript(mainBox, {
    // deferred questions become answerable right in the conversation…
    answer: async (qid, text) =>
      api(`/api/questions/${qid}/answer`, { method: "POST", body: { text } }),
    // …and subrun lines unfold into the child's own conversation, in place.
    loadSub: (n, o) => api(`/api/runs/${runId}/transcript?sub=${n}&offset=${o}`),
    isLive: () => !TERMINAL.has(curState),
    onRefer: setRef,
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
  viewingSub = (initialSub != null && (detail.subruns || []).includes(initialSub)) ? initialSub : null;
  renderSubBar();
  mainBox.hidden = viewingSub != null;
  subBox.hidden = viewingSub == null;

  // The main tail runs for the whole life of the view: transcript + state, reconnecting with
  // backoff and resuming from its last confirmed offset when the stream drops.
  tail = liveTail({
    page: (o) => `/api/runs/${runId}/transcript?offset=${o}`,
    events: (o) => `/api/runs/${runId}/events?offset=${o}`,
    offset: viewingSub == null ? initialOffset : 0,
    onEvent: (ev) => {
      if (ev.type === "subrun_start") { addSubTab(ev.payload.n, ev.payload.label); taskTree.refresh(); }
      if (ev.type === "subrun_end") taskTree.refresh();
      // a deliverable landed — the rail refreshes without waiting for run end
      if (ev.type === "observation" && !ev.payload?.error
          && (ev.payload?.kind === "write_file" || ev.payload?.kind === "edit_file")
          && String(ev.payload?.path || "").includes("artifacts/")) artifacts.refresh();
      if (ev.type === "observation" && ["read_file", "view_image", "write_file", "edit_file"]
          .includes(ev.payload?.kind)) fileActivity.poke();
      transcript.add(ev);
      if (viewingSub == null) scrollDown();
    },
    onState: (s) => {
      if (s.updated) lastUpdated = s.updated;
      setState(s.state);
      stateGraph.setPhase(s.phase);
      if (s.usage) usageSpan.textContent = fmtTokens(s.usage);
      if (s.model) setModel(s.model);
      showQuestion(s.question);
      if (TERMINAL.has(s.state)) { artifacts.refresh(); taskTree.refresh(); fileActivity.refresh(); }
    },
    onStatus: (s) => stream.set(s),
    onGone: () => stream.set("ended"),
  });
  if (viewingSub != null) mountSubPolling(viewingSub, initialOffset);

  // Manual scroll pauses following; scrolling back to the bottom resumes it (follow.js —
  // only an upward move pauses). The checkbox mirrors the live follow state.
  const stopFollow = followScroll({ margin: 60,
    pause: () => { if (followChk.checked) { followChk.checked = false; autoscroll = false; } },
    resume: () => { if (!followChk.checked) { followChk.checked = true; autoscroll = true; } },
  });

  return () => { if (tail) tail.stop(); stopSubPoll(); clearInterval(durTimer);
                 artifacts.destroy();
                 setup?.destroy();
                 stopFollow();
                 window.removeEventListener("rsched-bus", onBus); };
}
