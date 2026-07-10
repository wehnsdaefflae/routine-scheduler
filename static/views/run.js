// Run view: live SSE transcript (or drained past transcript), intervention controls, and a
// sub-run selector. Which sub-run you're reading — and the transcript offset — live in the URL
// (#/run/{id}?sub=N&offset=M), so a deep link reopens the exact view.

import { api, sse } from "/static/api.js";
import { setQuery } from "/static/router.js";
import { createTranscript } from "/static/components/transcript.js";
import { busy, chip, el, fmtTokens, fmtTs, toast } from "/static/util.js";

const TERMINAL = new Set(["finished", "failed", "aborted"]);

export async function render(view, runId, query = {}) {
  const [slug, ts] = runId.split(":");
  const initialSub = query.sub != null && query.sub !== "" ? Number(query.sub) : null;
  const initialOffset = Number(query.offset) || 0;

  const head = el("div", { class: "row spread" },
    el("h1", {}, el("a", { href: `#/routine/${slug}` }, slug), ` · run ${fmtTs(ts)}`),
    el("div", { class: "row" }));
  const stateChip = chip("…");
  const usageSpan = el("span", { class: "muted" });
  const controls = el("div", { class: "row" });
  head.lastChild.append(stateChip, usageSpan, controls);
  view.append(head);

  const questionBox = el("div", {});
  view.append(questionBox);

  // sub-run selector (main + each spawned child); hidden until there is at least one sub-run
  const subBar = el("div", { class: "row mt", hidden: true });
  view.append(subBar);

  const body = el("div", { class: "mt" });
  view.append(body);

  // "waiting for the model" spinner — lives at the BOTTOM of the conversation while the run works.
  const waitingBox = el("div", { class: "mt" });
  view.append(waitingBox);

  const injectRow = el("div", { class: "row mt" });
  const injectInput = el("input", { type: "text", placeholder: "inject a message into the run…", style: "flex:1" });
  const injectBtn = el("button", { class: "btn" }, "send");
  injectRow.append(injectInput, injectBtn);
  view.append(injectRow);

  // Auto-scroll ("follow"): on by default; the user can toggle it, and scrolling up pauses it.
  const followChk = el("input", { type: "checkbox", checked: true });
  followChk.onchange = () => { autoscroll = followChk.checked; if (autoscroll) scrollDown(); };
  view.append(el("label", { class: "row mt", style: "gap:6px;font-size:12px;color:var(--muted)" },
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
    } catch (err) { toast(err.message); resumeBtn.disabled = false; }
  };
  controls.append(pauseBtn, abortBtn, resumeBtn);

  // Live model + mid-run switch (applies at the next turn; the engine re-resolves every turn).
  const modelSpan = el("span", { class: "muted", style: "font-family:var(--mono);font-size:11.5px" });
  const switchBox = el("details", { style: "font-size:11.5px" },
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
      } catch (err) { toast(err.message); }
    };
    switchBox.append(el("div", { class: "row mt", style: "gap:5px" }, epSel, mIn, go));
  }).catch(() => {});
  head.lastChild.append(modelSpan, switchBox);

  // ---- transcript sources: main run = SSE; a sub-run = paged fetch + poll while active ---------
  let curState = "";
  const subs = new Map();          // n -> label
  let viewingSub = null;           // null = main, else sub-run number
  let source = null;               // the always-on main SSE (state, and main transcript)
  let subPoll = null, subOffset = 0;
  let transcript = createTranscript(body);
  let autoscroll = true;
  const scrollDown = () => { if (autoscroll) window.scrollTo(0, document.body.scrollHeight); };
  const setWaiting = (active) => {   // shown only for the main run (a sub-run is polled, not live)
    waitingBox.innerHTML = "";
    if (active && viewingSub == null) waitingBox.append(busy("waiting for the model…"));
  };

  function resetBody() { body.innerHTML = ""; transcript = createTranscript(body); }
  function stopSubPoll() { if (subPoll) { clearInterval(subPoll); subPoll = null; } }
  function closeSource() { if (source) { source.close(); source = null; } }

  function renderSubBar() {
    if (!subs.size) { subBar.hidden = true; subBar.innerHTML = ""; return; }
    subBar.hidden = false;
    subBar.innerHTML = "";
    subBar.append(el("span", { class: "muted", style: "font-family:var(--mono);font-size:11px" }, "transcript:"));
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
    resetBody();
    renderSubBar();
    setWaiting(["running", "starting", "queued"].includes(curState));   // main only; cleared for a sub
    if (n == null) reopenMainSSE(0);         // replay the main transcript into the fresh renderer
    else mountSubPolling(n, 0);
  }

  function reopenMainSSE(offset) {
    closeSource();
    source = sse(`/api/runs/${runId}/events?offset=${offset}`, {
      transcript: (ev) => {
        if (ev.type === "subrun_start") addSubTab(ev.payload.n, ev.payload.label);
        if (viewingSub == null) { transcript.add(ev); scrollDown(); }
      },
      state: (s) => {
        setState(s.state);
        if (s.usage) usageSpan.textContent = fmtTokens(s.usage);
        if (s.model) setModel(s.model);
        showQuestion(s.question);
      },
      end: () => closeSource(),
      onerror: () => {},
    });
  }

  function mountSubPolling(n, startOffset) {
    stopSubPoll();
    subOffset = startOffset || 0;
    const pull = async () => {
      try {
        const { events, offset } = await api(`/api/runs/${runId}/transcript?sub=${n}&offset=${subOffset}`);
        subOffset = offset;
        for (const ev of events) transcript.add(ev);
        scrollDown();
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
    switchBox.hidden = terminal;                     // no mid-run switch once the run has ended
    injectBtn.textContent = terminal ? "queue for next run" : "send";
    if (state === "paused") { paused = true; pauseBtn.textContent = "▶ resume"; }
    else if (paused && state !== "paused") { paused = false; pauseBtn.textContent = "⏸ pause"; }
    setWaiting(["running", "starting", "queued"].includes(state));   // the model is working
    scrollDown();
  }

  function showQuestion(q) {
    questionBox.innerHTML = "";
    if (!q) return;
    const input = el("input", { type: "text", placeholder: "your answer…", style: "flex:1" });
    const send = el("button", { class: "btn primary" }, "answer");
    const box = el("div", { class: "panel mt", style: "border-color:var(--warn)" },
      el("div", {}, `❓ ${q.question}`),
      q.options?.length ? el("div", { class: "row mt" },
        q.options.map((o) => el("button", { class: "btn small", onclick: () => { input.value = o; } }, o))) : null,
      el("div", { class: "row mt" }, input, send));
    send.onclick = async () => {
      if (!input.value.trim()) return;
      try {
        await api(`/api/questions/${q.qid}/answer`, { method: "POST", body: { text: input.value } });
        toast("answer sent");
        questionBox.innerHTML = "";
      } catch (err) { toast(err.message); }
    };
    questionBox.append(box);
  }

  pauseBtn.onclick = async () => {
    try {
      await api(`/api/runs/${runId}/${paused ? "resume" : "pause"}`, { method: "POST" });
    } catch (err) { toast(err.message); }
  };
  abortBtn.onclick = async () => {
    if (!confirm(`Abort ${runId}?`)) return;
    try { await api(`/api/runs/${runId}/abort`, { method: "POST" }); } catch (err) { toast(err.message); }
  };
  const doInject = async () => {
    if (!injectInput.value.trim()) return;
    try {
      const r = await api(`/api/runs/${runId}/inject`, { method: "POST", body: { text: injectInput.value } });
      toast(r.delivery === "mid-run" ? "injected — picked up at the next turn" : "queued for the next run");
      injectInput.value = "";
    } catch (err) { toast(err.message); }
  };
  injectBtn.onclick = doInject;
  injectInput.onkeydown = (e) => { if (e.key === "Enter") doInject(); };

  // ---- boot -----------------------------------------------------------------------------------
  const detail = await api(`/api/runs/${runId}`);
  setState(detail.state);
  usageSpan.textContent = fmtTokens(detail.usage);
  setModel(detail.model);
  showQuestion(detail.question);
  for (const n of detail.subruns || []) subs.set(n, `sub ${n}`);
  viewingSub = (initialSub != null && (detail.subruns || []).includes(initialSub)) ? initialSub : null;
  renderSubBar();
  if (viewingSub == null) {
    reopenMainSSE(initialOffset);
  } else {
    reopenMainSSE(0);                          // keep the state stream live while reading a sub
    mountSubPolling(viewingSub, initialOffset);
  }

  // Manual scroll pauses following; scrolling back to the bottom resumes it (the checkbox mirrors it).
  const onScroll = () => {
    const atBottom = window.innerHeight + window.scrollY >= document.body.scrollHeight - 60;
    if (atBottom !== followChk.checked) { followChk.checked = atBottom; autoscroll = atBottom; }
  };
  window.addEventListener("scroll", onScroll);

  return () => { closeSource(); stopSubPoll(); window.removeEventListener("scroll", onScroll); };
}
