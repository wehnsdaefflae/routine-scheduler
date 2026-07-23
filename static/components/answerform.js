// The ONE answer form — every surface that lets the user answer a question (Decisions
// page, run view, conversation, wizard, transcript inline, chat inline) builds its form
// here instead of hand-rolling a copy. The component owns the core: input + option
// buttons + default line + ask-back + submit + keyboard + draft persistence + error
// toast. Page chrome (meta chips, expires notes, lifecycle controls, settled states)
// stays with the host, which gets `{ node, input, submit, setSettled }` back — `input`
// so a host can manage focus order, `setSettled` so a bus event can close the form.

import { api } from "/static/api.js";
import { forgetField } from "/static/formpersist.js";
import { mdInline } from "/static/md.js";
import { el, toast, when } from "/static/util.js";

export function answerForm(q, {
  control = "textarea",        // "textarea" (Shift+Enter for newline) | "input" (Enter sends)
  placeholder = "your answer… (Shift+Enter for a new line)",
  numbered = false,            // option buttons labeled "1 · a" + digit keys 1-9 prefill
  defaultLine = true,          // "↪ without an answer: …" under the options (if q.default)
  askBack = false,             // the intermediate-reply button (submit(true))
  onArrow = null,              // (±1) => — ArrowUp/Down focus moves (Decisions page)
  submitText,                  // REQUIRED: async (text, intermediate) — the API call
  toastText = null,            // (intermediate) => string | null — success toast
  onSuccess = null,            // (text, intermediate) => — host's post-send behavior
  extraControls = null,        // node(s) beside the send button (lifecycle etc.)
} = {}) {
  const options = q.options || [];
  const input = control === "input"
    ? el("input", { type: "text", placeholder,
        "data-persist": `answer-${q.qid}`, style: "flex:1" })
    : el("textarea", { rows: "1", placeholder,
        "data-persist": `answer-${q.qid}`, style: "flex:1;resize:vertical" });
  const send = el("button", { class: "btn primary" }, "answer");
  const discuss = askBack ? el("button", { class: "btn",
    title: "send as a follow-up question / thought — the model replies and the question stays open" },
    "ask back") : null;
  const row = el("div", { class: "row mt" }, input, send, discuss, extraControls);
  const node = el("div", {},
    options.length ? el("div", { class: "row mt answer-opts", style: "gap:8px" },
      options.map((o, i) => el("button", {
        class: "btn small", ...(numbered ? { title: `press ${i + 1}` } : {}),
        onclick: () => { input.value = o; input.focus(); },
      }, numbered ? `${i + 1} · ${o}` : o))) : null,
    q.default && defaultLine ? el("div", { class: "faint small mt",
      title: "what the routine does if this stays unanswered" },
      `↪ without an answer: ${q.default}`) : null,
    row);

  const submit = async (intermediate = false) => {
    const text = input.value.trim();
    if (!text) return;
    send.disabled = true;
    if (discuss) discuss.disabled = true;
    try {
      await submitText(text, intermediate);
      forgetField(input);   // submitted — the draft must never refill this field
      const note = toastText?.(intermediate);
      if (note) toast(note);
      onSuccess?.(text, intermediate);
    } catch (err) {
      toast(err.message, 4000, { error: true });
      send.disabled = false;
      if (discuss) discuss.disabled = false;
    }
  };
  send.onclick = () => submit(false);
  if (discuss) discuss.onclick = () => submit(true);
  input.onkeydown = (e) => {
    if (e.key === "Enter" && (control === "input" || !e.shiftKey)) {
      e.preventDefault(); submit(false);
    } else if (onArrow && e.key === "ArrowDown") { e.preventDefault(); onArrow(1); }
    else if (onArrow && e.key === "ArrowUp") { e.preventDefault(); onArrow(-1); }
    else if (numbered && /^[1-9]$/.test(e.key) && !input.value && options[+e.key - 1]) {
      e.preventDefault(); input.value = options[+e.key - 1];
    }
  };

  const setSettled = (note) =>
    node.replaceChildren(el("span", { class: "faint small" }, note));
  return { node, input, submit, setSettled };
}


/** The blocking-question panel (run view + conversation — the same decision-record shape):
 * the ❓ prompt, the util-approval tag when the record is one, the timeout/Decisions line
 * when it expires, and the shared answer form. Renders into `box` (cleared first); a null
 * question just clears it.
 */
export function questionPanel(box, q, { onAnswered } = {}) {
  box.replaceChildren();
  if (!q) return;
  const form = answerForm(q, {
    submitText: (text, intermediate) => api(`/api/questions/${q.qid}/answer`,
      { method: "POST", body: { text, intermediate } }),
    askBack: true,
    toastText: (i) => (i ? "sent — the model will reply and re-ask" : "answer sent"),
    onSuccess: () => { box.replaceChildren(); onAnswered?.(); },
  });
  box.append(el("div", { class: "panel warn mt" },
    el("div", { class: "prose" },
      "❓ ", q.type === "util-approval" ? el("strong", {}, "[util approval] ") : null,
      mdInline(q.question || "")),
    q.expires ? el("div", { class: "faint small" },
      "the run continues without you ", when(q.expires, { mode: "rel" }),
      " — also answerable on the Decisions page",
      q.mirrored ? " and on Discord" : "") : null,
    form.node));
}
