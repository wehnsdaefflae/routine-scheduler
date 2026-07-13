// Run view: live transcript (resilient SSE tail with visible reconnect state), intervention
// controls, and a sub-run selector. Which sub-run you're reading — and the transcript offset —
// live in the URL (#/run/{id}?sub=N&offset=M), so a deep link reopens the exact view.

import { api } from "/static/api.js";
import { mdInline } from "/static/md.js";
import { setQuery } from "/static/router.js";
import { liveTail } from "/static/stream.js";
import { createTranscript } from "/static/components/transcript.js";
import { busy, chip, el, emptyState, fmtDur, fmtTokens, fmtTs, skeleton, streamStatus,
         toDate, toast, when } from "/static/util.js";
import { forgetField } from "/static/formpersist.js";

const TERMINAL = new Set(["finished", "failed", "aborted"]);
const WORKING = new Set(["running", "starting", "queued"]);

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

  const injectInput = el("input", { type: "text", placeholder: "inject a message into the run…", style: "flex:1" });
  const injectBtn = el("button", { class: "btn" }, "send");
  // Terminal runs only: wake THIS run back up with the message — the transcript is rehydrated
  // and the conversation continues in place, as often as you like.
  const converseBtn = el("button", { class: "btn primary", hidden: true }, "continue conversation");
  view.append(el("div", { class: "row mt" }, injectInput, converseBtn, injectBtn));

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
  api("/api/settings/endpoints").then((d) => {
    const eps = d.endpoints || [];
    if (!eps.length) return;
    const epSel = el("select", { style: "width:auto;font-size:11.5px;padding:3px 6px" },
      eps.map((e) => el("option", {}, e.name)));
    const mIn = el("input", { type: "text", placeholder: "model id",
      style: "width:150px;font-size:11.5px;padding:3px 6px" });
    const go = el("button", { class: "btn small primary" }, "switch");
    go.onclick = async () => {
      if (!mIn.value.trim()) { toast("enter a model id"); return; }
      try {
        const r = await api(`/api/runs/${runId}/model`, { method: "POST",
          body: { endpoint: epSel.value, model: mIn.value.trim() } });
        toast(`${r.switch} — takes effect next turn`);
      } catch (err) { toast(err.message, 4000, { error: true }); }
    };
    switchBox.append(el("div", { class: "row mt", style: "gap:5px" }, epSel, mIn, go));
  }).catch(() => {});
  controls.append(switchBox);

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
    injectBtn.textContent = terminal ? "queue for next run" : "send";
    converseBtn.hidden = !terminal;
    injectInput.placeholder = terminal
      ? "continue this conversation — or queue the message for the next run…"
      : "inject a message into the run…";
    tickDur();
    if (state === "paused") { paused = true; pauseBtn.textContent = "▶ resume"; }
    else if (paused && state !== "paused") { paused = false; pauseBtn.textContent = "⏸ pause"; }
    setWaiting(WORKING.has(state));                 // the model is working
    scrollDown();
  }

  function showQuestion(q) {
    questionBox.replaceChildren();
    if (!q) return;
    // data-persist keyed by qid: this question's draft is its own — never another's.
    const input = el("input", { type: "text", placeholder: "your answer…",
      "data-persist": `answer-${q.qid}`, style: "flex:1" });
    const send = el("button", { class: "btn primary" }, "answer");
    const discuss = el("button", { class: "btn",
      title: "send as a follow-up question / thought — the model replies and the question stays open" },
      "ask back");
    const box = el("div", { class: "panel warn mt" },
      el("div", { class: "prose" },
        "❓ ", q.type === "util-approval" ? el("strong", {}, "[util approval] ") : null,
        mdInline(q.question)),
      q.default ? el("div", { class: "faint small mt" }, `↪ without an answer: ${q.default}`) : null,
      q.expires ? el("div", { class: "faint small" },
        "the run continues without you ", when(q.expires, { mode: "rel" }),
        " — also answerable on the Decisions page",
        q.mirrored ? " and on Discord" : "") : null,
      q.options?.length ? el("div", { class: "row mt" },
        q.options.map((o) => el("button", { class: "btn small", onclick: () => { input.value = o; } }, o))) : null,
      el("div", { class: "row mt" }, input, send, discuss));
    const submit = async (intermediate) => {
      if (!input.value.trim()) return;
      try {
        await api(`/api/questions/${q.qid}/answer`, { method: "POST",
          body: { text: input.value, intermediate } });
        forgetField(input);   // sent — the draft must never refill
        toast(intermediate ? "sent — the model will reply and re-ask" : "answer sent");
        questionBox.replaceChildren();
      } catch (err) { toast(err.message, 4000, { error: true }); }
    };
    send.onclick = () => submit(false);
    discuss.onclick = () => submit(true);
    input.onkeydown = (e) => { if (e.key === "Enter") submit(false); };
    questionBox.append(box);
  }

  pauseBtn.onclick = async () => {
    try { await api(`/api/runs/${runId}/${paused ? "resume" : "pause"}`, { method: "POST" }); }
    catch (err) { toast(err.message, 4000, { error: true }); }
  };
  abortBtn.onclick = async () => {
    if (!confirm(`Abort ${runId}?`)) return;
    try { await api(`/api/runs/${runId}/abort`, { method: "POST" }); }
    catch (err) { toast(err.message, 4000, { error: true }); }
  };
  const doInject = async () => {
    if (!injectInput.value.trim()) return;
    try {
      const r = await api(`/api/runs/${runId}/inject`, { method: "POST", body: { text: injectInput.value } });
      toast(r.delivery === "mid-run" ? "injected — picked up at the next turn" : "queued for the next run");
      injectInput.value = "";
      forgetField(injectInput);   // sent — the draft must not refill on reload
    } catch (err) { toast(err.message, 4000, { error: true }); }
  };
  injectBtn.onclick = doInject;
  const doConverse = async () => {
    if (!injectInput.value.trim()) return;
    converseBtn.disabled = true;
    try {
      await api(`/api/runs/${runId}/converse`, { method: "POST", body: { text: injectInput.value } });
      forgetField(injectInput);   // delivered — must not refill after the reload below
      toast("message delivered — waking the run to continue the conversation…");
      setTimeout(() => location.reload(), 800);   // reattach the tail to the now-live run
    } catch (err) { toast(err.message, 5000, { error: true }); converseBtn.disabled = false; }
  };
  converseBtn.onclick = doConverse;
  // Enter = the primary action for the run's state: converse when terminal, inject when live.
  injectInput.onkeydown = (e) => {
    if (e.key === "Enter") (converseBtn.hidden ? doInject : doConverse)();
  };

  // ---- boot -----------------------------------------------------------------------------------
  let detail;
  try { detail = await api(`/api/runs/${runId}`); }
  catch (err) {
    mainBox.replaceChildren(emptyState("✕", "Run not found",
      `${err.message} — it may have been pruned by retention.`));
    return;
  }
  mainBox.replaceChildren();
  const transcript = createTranscript(mainBox, {
    // deferred questions become answerable right in the conversation…
    answer: async (qid, text) =>
      api(`/api/questions/${qid}/answer`, { method: "POST", body: { text } }),
    // …and subrun lines unfold into the child's own conversation, in place.
    loadSub: (n, o) => api(`/api/runs/${runId}/transcript?sub=${n}&offset=${o}`),
    isLive: () => !TERMINAL.has(curState),
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
      if (ev.type === "subrun_start") addSubTab(ev.payload.n, ev.payload.label);
      transcript.add(ev);
      if (viewingSub == null) scrollDown();
    },
    onState: (s) => {
      if (s.updated) lastUpdated = s.updated;
      setState(s.state);
      if (s.usage) usageSpan.textContent = fmtTokens(s.usage);
      if (s.model) setModel(s.model);
      showQuestion(s.question);
    },
    onStatus: (s) => stream.set(s),
    onGone: () => stream.set("ended"),
  });
  if (viewingSub != null) mountSubPolling(viewingSub, initialOffset);

  // Manual scroll pauses following; scrolling back to the bottom resumes it. Only an UPWARD
  // move pauses: content growth pushes the bottom away without any scroll of ours, and the old
  // symmetric check read that as "user left the bottom" — silently unchecking follow on every
  // busy run.
  let lastY = window.scrollY;
  const onScroll = () => {
    const y = window.scrollY;
    const up = y < lastY - 1;
    lastY = y;
    const atBottom = window.innerHeight + y >= document.body.scrollHeight - 60;
    if (up && !atBottom && followChk.checked) { followChk.checked = false; autoscroll = false; }
    else if (atBottom && !followChk.checked) { followChk.checked = true; autoscroll = true; }
  };
  window.addEventListener("scroll", onScroll);

  return () => { if (tail) tail.stop(); stopSubPoll(); clearInterval(durTimer);
                 window.removeEventListener("scroll", onScroll);
                 window.removeEventListener("rsched-bus", onBus); };
}
