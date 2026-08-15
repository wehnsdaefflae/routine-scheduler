// The ONE answer form — every surface that lets the user answer a question (Decisions
// page, run view, conversation, clarify session, transcript inline, chat inline) builds its form
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
  quick = false,               // buttons-only strip: the option / typed-decision buttons
  //                              submit as usual, but no free-text row and no ask-back.
  //                              For the chat/transcript inline rendering of a BLOCKING
  //                              question — the pinned panel keeps the one full form
  //                              (F264) while the decision stays one click away where the
  //                              user is actually reading (R132).
  onArrow = null,              // (±1) => — ArrowUp/Down focus moves (Decisions page)
  submitText,                  // REQUIRED: async (text, intermediate, decision) — the API call
  //                              (decision set = an access-request button; text is null then)
  toastText = null,            // (intermediate) => string | null — success toast
  onSuccess = null,            // (text, intermediate) => — host's post-send behavior
  extraControls = null,        // node(s) beside the send button (lifecycle etc.)
} = {}) {
  const options = q.options || [];
  // An ACCESS REQUEST (the record carries grant-entity ids): the typed decisions replace
  // free-form options. `recreate:` entities never offer "allow forever" — a fresh
  // deletion must always outrank an old grant, so that class is per-run only. Once-
  // grantable classes also offer "allow once": turn-action ones (action/util/runs/
  // workflows, D65) are revoked after exactly one matching action; secret/fs ones (D76)
  // are spent — coarser, as approved — by the next util invocation that receives them
  // (declared-env injection / mounted roots) or a file action under the fs root.
  // connection/machine grants stay four-state (a binding, not a spendable use).
  const request = Array.isArray(q.request) ? q.request : [];
  const ONCE_CLASSES = ["action:", "util:", "runs:", "workflows:",
    "secret:", "fs-read:", "fs-write:"];
  const onceOk = request.length > 0
    && request.every((e) => ONCE_CLASSES.some((p) => e.startsWith(p)));
  const DECISIONS = [
    ["allow_now", "allow now", "grant it for the asking run only — nothing persists"],
    ["allow_once", "allow once", "grant exactly ONE matching action — the engine revokes it the moment it is used"],
    ["allow_forever", "allow forever", "record the grant in the routine's config"],
    ["deny_now", "deny now", "decline for this run — the routine works without it"],
    ["deny_forever", "never", "decline forever — the routine stops asking for this"],
  ].filter(([key]) => (key !== "allow_forever" || !request.every((e) => e.startsWith("recreate:")))
    && (key !== "allow_once" || onceOk));
  // ALWAYS a textarea (user order 2026-08-15, F346): an answer is prose, and a one-line
  // slot punishes any thought longer than a word. `control` now ONLY sets the Enter
  // behavior ("input": Enter always sends; "textarea": Shift+Enter breaks the line).
  // Flex lives on the .answer-input class, not inline — inline flex would beat the
  // mobile full-width stylesheet rule (F238).
  const input = el("textarea", { rows: "1", placeholder,
    "data-persist": `answer-${q.qid}`, class: "answer-input" });
  const send = el("button", { class: "btn primary" }, "answer");
  const discuss = askBack && !quick ? el("button", { class: "btn",
    title: "send as a follow-up question / thought — the model replies and the question stays open" },
    "ask back") : null;
  // quick mode drops the free-text row (input still backs the option buttons' submit path)
  const row = quick ? null : el("div", { class: "row mt" }, input, send, discuss, extraControls);
  const decide = async (decision, btnRow) => {
    for (const b of btnRow.querySelectorAll("button")) b.disabled = true;
    try {
      await submitText(null, false, decision);
      forgetField(input);
      const phrase = DECISIONS.find(([key]) => key === decision)?.[1] || decision;
      const note = toastText?.(false);
      if (note) toast(note);
      onSuccess?.(phrase, false);
    } catch (err) {
      if (err.status === 404) {   // already resolved elsewhere (answered on another surface,
        // expired, or the run moved on) — a benign end-state. Settle the card instead of a red
        // error toast (which also logs a UI-friction trace event) + re-enabled buttons that
        // only invite a doomed retry (F259).
        toast("already answered elsewhere");
        const phrase = DECISIONS.find(([key]) => key === decision)?.[1] || decision;
        onSuccess?.(phrase, false);
        return;
      }
      toast(err.message, 4000, { error: true });
      for (const b of btnRow.querySelectorAll("button")) b.disabled = false;
    }
  };
  const decisionRow = request.length ? (() => {
    const btnRow = el("div", { class: "row mt answer-opts", style: "gap:8px" });
    for (const [key, label, help] of DECISIONS) {
      btnRow.append(el("button", {
        class: `btn small${key === "allow_forever" ? " primary" : ""}`, title: help,
        onclick: () => decide(key, btnRow),
      }, label));
    }
    return el("div", {},
      el("div", { class: "row mt", style: "gap:6px;flex-wrap:wrap" },
        el("span", { class: "faint small" }, "requests access to:"),
        request.map((e) => el("code", { class: "small" }, e))),
      btnRow);
  })() : null;
  const node = el("div", {},
    decisionRow,
    !request.length && options.length ? el("div", { class: "row mt answer-opts", style: "gap:8px" },
      options.map((o, i) => el("button", {
        class: "btn small", ...(numbered ? { title: `press ${i + 1}` } : {}),
        // one-click decision (F189): clicking an option SUBMITS it — free text stays
        // possible via the input; digit keys still only prefill (editable before Enter).
        onclick: () => { input.value = o; submit(false); },
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
      if (err.status === 404) {   // already resolved elsewhere — benign end-state, not an error (F259)
        toast("already answered elsewhere");
        forgetField(input);
        onSuccess?.(text, intermediate);
        return;
      }
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
    submitText: (text, intermediate, decision) => api(`/api/questions/${q.qid}/answer`,
      { method: "POST", body: decision ? { decision } : { text, intermediate } }),
    askBack: true,
    toastText: (i) => (i ? "sent — the model will reply and re-ask" : "answer sent"),
    onSuccess: () => { box.replaceChildren(); onAnswered?.(); },
  });
  box.append(el("div", { class: "panel warn mt" },
    el("div", { class: "prose" },
      "❓ ", q.type === "util-approval" ? el("strong", {}, "[util approval] ")
        : q.type === "request" ? el("strong", {}, "[access request] ") : null,
      mdInline(q.question || "")),
    q.expires ? el("div", { class: "faint small" },
      "the run continues without you ", when(q.expires, { mode: "rel" }),
      " — also answerable on the Decisions page",
      q.mirrored ? " and on Discord" : "") : null,
    form.node));
}
