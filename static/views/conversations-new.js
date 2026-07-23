// The new-conversation composer (the #/conversations no-slug mount): first message,
// playbook picker, pre-start model/budgets/permissions - split from conversations.js.
// PREFILL_KEY carries the last user text of a forked ([new-topic]) conversation over.

import { api, apiUpload } from "/static/api.js";
import { deliberationControl } from "/static/components/deliberation.js";
import { filePicker } from "/static/components/filepicker.js";
import { permissionsPanel } from "/static/components/permissions.js";
import { forgetField } from "/static/formpersist.js";
import { navigate } from "/static/router.js";
import { el, toast } from "/static/util.js";

export const PREFILL_KEY = "conv-new-prefill";

export function mountComposerOnly(main) {
  const text = el("textarea", { rows: 5,
    placeholder: "What should the agent do? The first message becomes the conversation's task…" });
  const prefill = sessionStorage.getItem(PREFILL_KEY);
  if (prefill) { text.value = prefill; sessionStorage.removeItem(PREFILL_KEY); }
  // Playbook picker (the use-instruction analog): a picked playbook's brief seeds the
  // conversation; the first-message box then just SPECIALIZES it, and may be left empty.
  const pbSel = el("select", { "data-nopersist": "" },
    el("option", { value: "" }, "no playbook · start fresh"));
  const pbHint = el("div", { class: "faint small" });
  let pbList = [];
  api("/api/playbooks").then((r) => {
    pbList = r.playbooks || [];
    pbList.forEach((p) => pbSel.append(el("option", { value: p.slug }, p.title || p.slug)));
  }).catch(() => { /* library unreachable — picker stays empty, plain conversation still works */ });
  pbSel.onchange = () => {
    const p = pbList.find((x) => x.slug === pbSel.value);
    pbHint.textContent = p ? `▸ ${p.when || ""}${p.axis ? `  ·  varies: ${p.axis}` : ""}` : "";
    text.placeholder = pbSel.value
      ? "Optional — anything specific for this run? The playbook is the brief…"
      : "What should the agent do? The first message becomes the conversation's task…";
  };
  const workdir = el("input", { type: "text", placeholder: "~/path/to/project (optional)" });
  // Pre-start budgets: per-REPLY ceilings + a cumulative cap over the WHOLE conversation
  // (all optional — blank keeps the default; -1 = unlimited).
  const turnsIn = el("input", { type: "number", min: "-1", step: "1", placeholder: "10",
    style: "width:80px", title: "max turns per reply (-1 = unlimited)" });
  const totalTurnsIn = el("input", { type: "number", min: "-1", step: "1", placeholder: "∞",
    style: "width:80px", title: "max turns for the whole conversation (blank or -1 = unlimited)" });
  const minsIn = el("input", { type: "number", min: "-1", step: "1", placeholder: "30",
    style: "width:80px", title: "max minutes per reply (-1 = unlimited)" });
  const tokIn = el("input", { type: "number", min: "-1", step: "1", placeholder: "400000",
    style: "width:100px", title: "max tokens per reply (-1 = unlimited)" });
  // Pre-start model picker: pick a catalog model by NAME (or fall back to the system model),
  // so a conversation can start on the right model instead of system-default-then-switch.
  const modelSel = el("select", { "data-nopersist": "" },
    el("option", { value: "" }, "default · system model"));
  api("/api/settings/models").then((r) => {
    if (r.system_model) modelSel.options[0].textContent = `default · ${r.system_model}`;
    (r.models || []).forEach((m) => modelSel.append(el("option", { value: m.name }, m.name)));
  }).catch(() => { /* settings unreachable — the default option still works */ });
  // ⚙ capabilities & budgets — the SAME surface the conversation header offers, but
  // BEFORE create: the first reply fires on create, so a permission, budget, or
  // deliberation level that must govern reply #1 has to be set here (afterwards the
  // header panel takes over). Fed by /api/conversations/defaults; the collected
  // permission payload rides the create request.
  const delib = deliberationControl("deliberate");
  let permPanel = null;   // {node, value} once the defaults load
  const capsBody = el("div", { class: "conv-opts" },
    el("label", {}, "project directory — the agent may read & edit it", workdir),
    el("div", { class: "row mt", style: "gap:10px;align-items:flex-start" },
      el("span", { class: "faint small", style: "min-width:150px;padding-top:4px" },
        "deliberation — thinking on paper"),
      delib.node));
  api("/api/conversations/defaults").then((d) => {
    if (d.deliberation) delib.set(d.deliberation);
    const b = d.budgets || {};
    if (b.max_turns != null) turnsIn.placeholder = String(b.max_turns);
    if (b.max_wall_clock_min != null) minsIn.placeholder = String(b.max_wall_clock_min);
    if (b.max_total_tokens != null) tokIn.placeholder = String(b.max_total_tokens);
    permPanel = permissionsPanel(d.permissions, d.capabilities, {
      disableRuns: "a conversation is one continuous run — previous-run depth is routine-only" });
    capsBody.append(el("div", { class: "mt" }), permPanel.node);
  }).catch(() => {
    capsBody.append(el("div", { class: "muted small mt" },
      "permission defaults unavailable — the conversation starts with the standard set; ",
      "tune it in the header panel afterwards"));
  });
  const { picker, files, clearFiles, wirePaste } = filePicker();
  wirePaste(text);
  const send = el("button", { class: "btn primary" }, "start conversation");
  send.onclick = async () => {
    if (!text.value.trim() && !pbSel.value) { toast("write the first message or pick a playbook"); return; }
    send.disabled = true;
    try {
      const fd = new FormData();
      fd.append("text", text.value);
      if (pbSel.value) fd.append("playbook", pbSel.value);
      if (modelSel.value) fd.append("model", modelSel.value);
      if (workdir.value.trim()) fd.append("workdir", workdir.value.trim());
      if (turnsIn.value.trim()) fd.append("max_turns", turnsIn.value.trim());
      if (totalTurnsIn.value.trim()) fd.append("max_total_turns", totalTurnsIn.value.trim());
      if (minsIn.value.trim()) fd.append("max_wall_clock_min", minsIn.value.trim());
      if (tokIn.value.trim()) fd.append("max_total_tokens", tokIn.value.trim());
      fd.append("deliberation", delib.value);
      if (permPanel) fd.append("permissions", JSON.stringify(permPanel.value()));
      for (const f of files()) fd.append("files", f);
      const r = await apiUpload("/api/conversations", fd);
      forgetField(text); forgetField(workdir);   // submitted — never refill the next composer
      clearFiles();
      navigate(`#/conversations/${r.slug}`);
    } catch (err) { toast(err.message, 5000, { error: true }); send.disabled = false; }
  };
  main.replaceChildren(
    el("div", { class: "page-head" }, el("div", {},
      el("div", { class: "kicker" }, "conversations"),
      el("h1", {}, "New conversation"))),
    el("div", { class: "panel conv-new" },
      text,
      el("div", { class: "row mt", style: "gap:8px;align-items:center;flex-wrap:wrap" },
        el("span", { class: "faint small" }, "playbook"), pbSel),
      pbHint,
      el("div", { class: "row mt", style: "gap:8px;flex-wrap:wrap" }, picker, send),
      el("div", { class: "row mt", style: "gap:8px;align-items:center" },
        el("span", { class: "faint small" }, "model"), modelSel),
      el("div", { class: "row mt", style: "gap:12px;align-items:center;flex-wrap:wrap" },
        el("span", { class: "faint small" }, "budget"),
        el("label", { class: "faint small row", style: "gap:4px;align-items:center" },
          "turns / reply", turnsIn),
        el("label", { class: "faint small row", style: "gap:4px;align-items:center" },
          "minutes / reply", minsIn),
        el("label", { class: "faint small row", style: "gap:4px;align-items:center" },
          "tokens / reply", tokIn),
        el("label", { class: "faint small row", style: "gap:4px;align-items:center" },
          "whole conversation (turns)", totalTurnsIn)),
      el("details", { class: "mt small" },
        el("summary", { style: "cursor:pointer;color:var(--muted)" },
          "⚙ capabilities & budgets · project dir, permissions, deliberation"),
        capsBody),
      el("div", { class: "faint small mt" },
        "pick a model above or start on the system default — switch it any time at the top of the conversation")));
  text.focus();
}
