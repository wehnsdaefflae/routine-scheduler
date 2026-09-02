// The new-conversation composer (the #/conversations no-slug mount): first message,
// playbook picker, and the pre-start settings — model, budgets, deliberation, project
// directory and permissions — split from conversations.js. PREFILL_KEY carries the last
// user text of a forked ([new-topic]) conversation over.
//
// D57: the pre-start settings are laid out with the SAME titled-section vocabulary the
// routine config page uses (components/settings-section.js), so a setting reads and looks
// the same on both surfaces. Only the fields a conversation actually submits are shown here;
// a conversation has no schedule, triggers or retention, so those routine sections are absent.

import { api, apiUpload } from "/static/api.js";
import { deliberationControl } from "/static/components/deliberation.js";
import { adminToggle } from "/static/components/admintoggle.js";
import { filePicker } from "/static/components/filepicker.js";
import { rootsEditor } from "/static/components/fsroots.js";
import { connectionsCard } from "/static/components/connections.js";
import { abilitiesPanel } from "/static/components/abilities.js";
import { rulePicker } from "/static/components/rulepicker.js";
import { settingsSection } from "/static/components/settings-section.js";
import { forgetField } from "/static/formpersist.js";
import { navigate } from "/static/router.js";
import { el, modelOption, skeleton, toast } from "/static/util.js";

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
  const workdir = el("input", { type: "text", placeholder: "~/path/to/project (optional)",
    style: "width:100%;max-width:420px" });
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
  // Options carry the model's context window and disable ones the harness cannot run
  // (R112/R128 — the create endpoint refuses those too; the picker says so up front).
  const roleSel = (fallbackLabel, title) => el("select", { "data-nopersist": "", title },
    el("option", { value: "" }, fallbackLabel));
  const modelSel = roleSel("default · system model", "main model");
  const toolSel = roleSel("↳ same as main", "tool-call model");
  const uncSel = roleSel("none · uncensored off", "uncensored model — where refused requests are delivered");
  api("/api/settings/models").then((r) => {
    if (r.system_model) modelSel.options[0].textContent = `default · ${r.system_model}`;
    (r.models || []).forEach((m) => {
      modelSel.append(modelOption(m.name, m.window));
      toolSel.append(modelOption(m.name, m.window));
      uncSel.append(modelOption(m.name, m.window));
    });
  }).catch(() => { /* settings unreachable — the default option still works */ });
  // D70: folder access granted at CREATE time — the roots land on the conversation's
  // config before the engine boots, so reply #1 already has them (the workdir above
  // stays the project directory; these are extra grants, e.g. a data folder).
  const readRoots = rootsEditor([], { pickTitle: "read-only folder" });
  const writeRoots = rootsEditor([], { pickTitle: "read + write folder" });
  // Permissions + deliberation govern reply #1, which fires on create — so they must be set
  // here (afterwards the conversation header panel takes over). Fed by /api/conversations/defaults.
  const delib = deliberationControl("deliberate");
  const permsHost = el("div", {});   // the permissions panel appends here once defaults load
  let permPanel = null;
  // F339: rules and connections are PRE-START choices too. A rule especially — it reaches the
  // prompt through main.md's Standing-practices tail, materialized at create time, so one
  // bound afterwards never governs reply #1, which fires the moment you send.
  const rulesHost = el("div", {}, skeleton(["60%"]));
  let rulePick = null;
  let pickedConnections = {};
  api("/api/conversations/defaults").then((d) => {
    if (d.deliberation) delib.set(d.deliberation);
    const b = d.budgets || {};
    if (b.max_turns != null) turnsIn.placeholder = String(b.max_turns);
    if (b.max_wall_clock_min != null) minsIn.placeholder = String(b.max_wall_clock_min);
    if (b.max_total_tokens != null) tokIn.placeholder = String(b.max_total_tokens);
    permPanel = abilitiesPanel(d.permissions, d.capabilities, {
      disableRuns: "a conversation is one continuous run — previous-run depth is routine-only" });
    permsHost.replaceChildren(permPanel.node);
    // no onSave → the picker renders no apply button; its `selected` rides the form
    rulePick = rulePicker(d.library_rules || [], d.rules || []);
    rulesHost.replaceChildren(rulePick.node);
  }).catch(() => {
    permsHost.replaceChildren(el("div", { class: "muted small" },
      "permission defaults unavailable — the conversation starts with the standard set; ",
      "tune it in the header panel after it is created"));
  });
  const { picker, files, clearFiles, wirePaste } = filePicker();
  wirePaste(text);
  // D66: the Admin toggle on the CREATE composer — reply #1 fires on create, so admin must be
  // armable HERE; the per-conversation toggle only governs the messages after that. The server
  // re-validates the token and drops the one-shot marker on the first run.
  const admin = adminToggle({
    title: "start this conversation with the full toolset — sends the admin token on create",
    prompt: "Admin token — lifts capability gating for this conversation, starting with the first "
      + "reply. Stored for this browser session only; the server re-checks it on every request.",
    onMsg: "admin on — this conversation starts with the full toolset",
    offMsg: "admin off — the conversation starts normally",
  });
  const send = el("button", { class: "btn primary" }, "start conversation");
  send.onclick = async () => {
    if (!text.value.trim() && !pbSel.value) { toast("write the first message or pick a playbook"); return; }
    send.disabled = true;
    try {
      const fd = new FormData();
      fd.append("text", text.value);
      if (pbSel.value) fd.append("playbook", pbSel.value);
      // Model roles: if only main is picked, send the `model` shorthand (seeds main +
      // tool_call). If tool_call or the uncensored role is set too, send the
      // full per-role `models` map so a conversation can START with an uncensored model configured.
      if (toolSel.value || uncSel.value) {
        const roles = {};
        if (modelSel.value) { roles.main = modelSel.value; roles.tool_call = modelSel.value; }
        if (toolSel.value) roles.tool_call = toolSel.value;
        if (uncSel.value) roles.uncensored = uncSel.value;
        fd.append("models", JSON.stringify(roles));
      } else if (modelSel.value) {
        fd.append("model", modelSel.value);
      }
      if (workdir.value.trim()) fd.append("workdir", workdir.value.trim());
      if (readRoots.value().length) fd.append("fs_read_roots", JSON.stringify(readRoots.value()));
      if (writeRoots.value().length) fd.append("fs_write_roots", JSON.stringify(writeRoots.value()));
      if (turnsIn.value.trim()) fd.append("max_turns", turnsIn.value.trim());
      if (totalTurnsIn.value.trim()) fd.append("max_total_turns", totalTurnsIn.value.trim());
      if (minsIn.value.trim()) fd.append("max_wall_clock_min", minsIn.value.trim());
      if (tokIn.value.trim()) fd.append("max_total_tokens", tokIn.value.trim());
      fd.append("deliberation", delib.value);
      if (permPanel) fd.append("permissions", JSON.stringify(permPanel.value()));
      if (rulePick) fd.append("rules", JSON.stringify(rulePick.selected));
      if (Object.keys(pickedConnections).length)
        fd.append("connections", JSON.stringify(pickedConnections));
      for (const f of files()) fd.append("files", f);
      const r = await apiUpload("/api/conversations", fd, admin.headers());
      forgetField(text); forgetField(workdir);   // submitted — never refill the next composer
      clearFiles();
      navigate(`#/conversations/${r.slug}`);
    } catch (err) { toast(err.message, 5000, { error: true }); send.disabled = false; }
  };

  const budgetRow = el("div", { class: "row", style: "gap:12px;align-items:center;flex-wrap:wrap" },
    el("label", { class: "faint small row", style: "gap:4px;align-items:center" },
      "turns / reply", turnsIn),
    el("label", { class: "faint small row", style: "gap:4px;align-items:center" },
      "minutes / reply", minsIn),
    el("label", { class: "faint small row", style: "gap:4px;align-items:center" },
      "tokens / reply", tokIn),
    el("label", { class: "faint small row", style: "gap:4px;align-items:center" },
      "whole conversation (turns)", totalTurnsIn));

  main.replaceChildren(
    el("div", { class: "page-head" }, el("div", {},
      el("h1", {}, "New conversation"))),
    // the primary action: the first message, an optional playbook, and start
    el("div", { class: "panel conv-new" },
      text,
      el("div", { class: "row mt", style: "gap:8px;align-items:center;flex-wrap:wrap" },
        el("span", { class: "faint small" }, "playbook"), pbSel),
      pbHint,
      el("div", { class: "row mt", style: "gap:8px;flex-wrap:wrap" }, picker, admin.node, send)),
    // the pre-start settings — the same titled-section vocabulary the routine page uses
    ...settingsSection("Model",
      "Which model answers this conversation — pick one from the catalog or start on the "
      + "system default. You can switch the main and uncensored models any time from the top "
      + "of the conversation; the tool-call model can only be set here, before the "
      + "conversation starts. The uncensored role is where the refusal-handling machinery "
      + "hands a refused request's essence, so leave it off unless you want that.",
      el("div", { class: "col", style: "gap:8px" },
        el("label", { class: "faint small row", style: "gap:6px;align-items:center" },
          "main", modelSel),
        el("label", { class: "faint small row", style: "gap:6px;align-items:center" },
          "tool-call", toolSel),
        el("label", { class: "faint small row", style: "gap:6px;align-items:center" },
          "uncensored", uncSel))),
    ...settingsSection("Project directory",
      "A folder on the server the agent may read and edit for this conversation. Leave empty "
      + "to keep it sandboxed to the conversation's own directory.",
      workdir),
    ...settingsSection("Folder access",
      "Extra folders this conversation may use, granted BEFORE the first reply fires — so "
      + "reply #1 already has them instead of asking mid-run. Read + write folders are also "
      + "readable; paths need not exist yet.",
      el("div", {},
        el("div", { class: "faint small" }, "read + write"), writeRoots.node,
        el("div", { class: "faint small mt" }, "read-only"), readRoots.node)),
    ...settingsSection("Budgets",
      "Optional ceilings. The per-reply limits bound one turn of the agent's work; the "
      + "whole-conversation cap bounds the entire thread. Blank keeps the default; -1 means unlimited.",
      budgetRow),
    ...settingsSection("Deliberation",
      "How much of the model's reasoning is written down as it works — more paper is easier to "
      + "follow but costs tokens.",
      delib.node),
    ...settingsSection("General rules",
      "The shared practices this conversation holds. They are woven into its recipe when it "
      + "is created, so pick them NOW — a rule bound afterwards does not govern the first "
      + "reply, which fires as soon as you send.",
      rulesHost),
    ...settingsSection("Connections",
      "Bind an OAuth account per provider so the first reply can already act as that "
      + "account, instead of hitting an unbound connection and having to ask. Connect the "
      + "accounts themselves in Settings \u2192 Connections.",
      connectionsCard({}, { onChange: (c) => { pickedConnections = c; } })),
    ...settingsSection("Permissions & capabilities",
      "What this conversation is allowed to do — enforced by the engine on every action. These "
      + "govern the first reply, which fires as soon as you start, so set them here; you can "
      + "adjust them afterward from the conversation header.",
      permsHost));
  text.focus();
}
