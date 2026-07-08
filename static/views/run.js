// Run view: live SSE transcript (or drained past transcript), intervention controls.

import { api, sse } from "/static/api.js";
import { createTranscript } from "/static/components/transcript.js";
import { chip, el, fmtTokens, fmtTs, toast } from "/static/util.js";

export async function render(view, runId) {
  const [slug, ts] = runId.split(":");
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

  const body = el("div", { class: "mt" });
  view.append(body);
  const transcript = createTranscript(body);

  const injectRow = el("div", { class: "row mt" });
  const injectInput = el("input", { type: "text", placeholder: "inject a message into the run…", style: "flex:1" });
  const injectBtn = el("button", { class: "btn" }, "send");
  injectRow.append(injectInput, injectBtn);
  view.append(injectRow);

  let paused = false;
  const pauseBtn = el("button", { class: "btn small" }, "⏸ pause");
  const abortBtn = el("button", { class: "btn small danger" }, "✕ abort");
  controls.append(pauseBtn, abortBtn);

  function setState(state) {
    stateChip.textContent = state;
    stateChip.className = `chip ${state}`;
    const terminal = ["finished", "failed", "aborted"].includes(state);
    pauseBtn.disabled = abortBtn.disabled = terminal;
    injectBtn.textContent = terminal ? "queue for next run" : "send";
    if (state === "paused") { paused = true; pauseBtn.textContent = "▶ resume"; }
    else if (paused && state !== "paused") { paused = false; pauseBtn.textContent = "⏸ pause"; }
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

  const detail = await api(`/api/runs/${runId}`);
  setState(detail.state);
  usageSpan.textContent = fmtTokens(detail.usage);
  showQuestion(detail.question);

  const source = sse(`/api/runs/${runId}/events`, {
    transcript: (ev) => {
      transcript.add(ev);
      if (autoscroll) window.scrollTo(0, document.body.scrollHeight);
    },
    state: (st) => {
      setState(st.state);
      if (st.usage) usageSpan.textContent = fmtTokens(st.usage);
      showQuestion(st.question);
    },
    end: () => source.close(),
    onerror: () => {},
  });

  let autoscroll = true;
  const onScroll = () => {
    autoscroll = window.innerHeight + window.scrollY >= document.body.scrollHeight - 60;
  };
  window.addEventListener("scroll", onScroll);

  return () => { source.close(); window.removeEventListener("scroll", onScroll); };
}
