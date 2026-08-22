// The conversation header: editable title, tags, delete, and the capabilities panel
// (budgets, deliberation, permissions, practice modules) + the model switcher - split
// from conversations.js. onListChanged refreshes the sidebar after title/tag edits.

import { api } from "/static/api.js";
import { connectionsCard } from "/static/components/connections.js";
import { deliberationControl } from "/static/components/deliberation.js";
import { confirmDialog } from "/static/components/dialog.js";
import { rootsEditor } from "/static/components/fsroots.js";
import { permissionsPanel } from "/static/components/permissions.js";
import { tagsEditor } from "/static/components/tags.js";
import { rulePicker } from "/static/components/rulepicker.js";
import { navigate } from "/static/router.js";
import { el, modelOption, toast } from "/static/util.js";

// The model line at the top of a conversation: shows the EFFECTIVE main model and the
// honeypot (uncensored) role, and switches EITHER at any point — routine.yaml is patched
// (each reply boots on it), and a live reply additionally gets the mid-run control.json
// switch, per role. The honeypot is a normal uncensored model to the engine; the refusal
// machinery only fires when it is set, so it must be switchable here, not only at create.
// Options carry each model's context window from `catalog_meta`, and a model whose window
// cannot run the harness is disabled (R112/R128 — the PATCH refuses it anyway; the picker
// says so up front instead of erroring after the click).
function modelControl(detail, slug, isLive) {
  const sysLabel = detail.system_model || "system model";
  const meta = detail.catalog_meta || {};
  const mkSel = (cur, fallback, title) => el("select",
    { title, style: "width:auto;font-size:11.5px;padding:3px 6px" },
    el("option", { value: "" }, fallback),
    (detail.catalog || []).map((n) => modelOption(n, meta[n], { selected: cur === n || null })));
  const mainSel = mkSel(detail.models?.main || "", `default · ${sysLabel}`, "main model");
  const honSel = mkSel(detail.models?.uncensored || "", "none · honeypot off",
    "honeypot (uncensored) model — where refused requests are delivered");
  const apply = el("button", { class: "btn small primary", hidden: true }, "apply");
  const show = () => { apply.hidden = false; };
  mainSel.onchange = show; honSel.onchange = show;
  apply.onclick = async () => {
    const mainName = mainSel.value, honName = honSel.value;
    // wholesale-replace semantics (blank clears a role) — send the FULL role set so
    // switching one role never drops the other; main seeds tool_call, as the composer does.
    const models = {};
    if (mainName) { models.main = mainName; models.tool_call = mainName; }
    if (honName) models.uncensored = honName;
    try {
      await api(`/api/conversations/${slug}`, { method: "PATCH", body: { models } });
      if (isLive() && detail.run_id) {
        // a live reply switches each role at its next turn boundary
        if (mainName) await api(`/api/runs/${detail.run_id}/model`,
          { method: "POST", body: { model: mainName, kind: "main" } }).catch(() => {});
        if (honName) await api(`/api/runs/${detail.run_id}/model`,
          { method: "POST", body: { model: honName, kind: "uncensored" } }).catch(() => {});
      }
      toast(`model → ${mainName || sysLabel}${honName ? ` · honeypot → ${honName}` : ""}`);
      apply.hidden = true;
    } catch (err) { toast(err.message, 4000, { error: true }); }
  };
  return el("span", { class: "conv-model" },
    el("span", { class: "faint small" }, "model"), mainSel,
    el("span", { class: "faint small" }, "honeypot"), honSel, apply);
}

export function renderHead(head, detail, stateChip, { slug, isLive, onListChanged }) {
  const title = el("h1", { class: "conv-h1", contenteditable: "plaintext-only",
    spellcheck: "false" }, detail.title || slug);
  title.onblur = async () => {
    const t = title.textContent.trim();
    if (!t || t === detail.title) return;
    try { await api(`/api/conversations/${slug}`, { method: "PATCH", body: { title: t } }); onListChanged(); }
    catch (err) { toast(err.message, 4000, { error: true }); }
  };
  const tagsRow = el("span", { class: "conv-tagline" },
    tagsEditor(detail.tags, async (next) => {
      await api(`/api/conversations/${slug}`, { method: "PATCH", body: { tags: next } });
      onListChanged();
    }, { placeholder: "add tag…" }));
  const del = el("button", { class: "btn small danger" }, "delete");
  del.onclick = async () => {
    if (!(await confirmDialog("Delete this conversation? It is unversioned — this cannot be undone.", { confirmLabel: "delete" }))) return;
    try { await api(`/api/conversations/${slug}`, { method: "DELETE" }); navigate("#/conversations"); }
    catch (err) { toast(err.message, 4000, { error: true }); }
  };
  // capabilities: budgets (per-reply ceilings) + permission toggles (routine-only ones
  // greyed) + rules read-only
  const caps = el("details", { class: "small conv-caps" },
    el("summary", { style: "cursor:pointer;color:var(--muted)" },
      `⚙ capabilities & budgets${detail.workdir ? ` · project: ${detail.workdir}` : ""}`));
  const capBody = el("div", { class: "conv-opts" });
  const b = detail.budgets || {};
  const numIn = (v, min = "1") => el("input", { type: "number", min, value: v,
    style: "width:90px;font-size:11.5px;padding:3px 6px" });
  // fallbacks mirror CONVERSATION_BUDGETS (conversations.py) — a runaway backstop, not a pace
  const turnsIn = numIn(b.max_turns ?? 40);
  const minsIn = numIn(b.max_wall_clock_min ?? 60, "-1");    // -1 = unlimited time
  const tokIn = numIn(b.max_total_tokens ?? 400000, "-1");   // -1 = unlimited tokens
  const saveBudgets = el("button", { class: "btn small" }, "save budgets");
  saveBudgets.onclick = async () => {
    try {
      await api(`/api/conversations/${slug}`, { method: "PATCH", body: { budgets: {
        max_turns: +turnsIn.value || 40, max_wall_clock_min: +minsIn.value || 60,
        max_total_tokens: +tokIn.value || 400000 } } });
      toast("budgets saved — they cap EACH reply, from the next one");
    } catch (err) { toast(err.message, 4000, { error: true }); }
  };
  const budgetField = (label, input) => el("label", { style: "flex-direction:column" },
    el("span", { class: "faint" }, label), input);
  capBody.append(el("div", { class: "row", style: "gap:12px;flex-wrap:wrap;align-items:flex-end" },
    budgetField("turns / reply", turnsIn), budgetField("minutes / reply (-1=∞)", minsIn),
    budgetField("tokens / reply (-1=∞)", tokIn), saveBudgets));
  // Folder access (D82): the same read/write roots the composer grants at create time,
  // editable mid-conversation. Saved to config wholesale; they reach the NEXT reply's
  // boot (a live reply keeps the roots it booted with) — same contract as budgets above.
  const readRoots = rootsEditor(detail.fs_read_roots, { pickTitle: "add a read root" });
  const writeRoots = rootsEditor(detail.fs_write_roots, { pickTitle: "add a write root" });
  const saveRoots = el("button", { class: "btn small" }, "save folder access");
  saveRoots.onclick = async () => {
    try {
      await api(`/api/conversations/${slug}`, { method: "PATCH", body: {
        fs_read_roots: readRoots.value(), fs_write_roots: writeRoots.value() } });
      toast("folder access saved — applies from the next reply");
    } catch (err) { toast(err.message, 4000, { error: true }); }
  };
  capBody.append(el("div", { class: "mt" },
    el("div", { class: "faint small" }, "folder access — directories the conversation may "
      + "use beyond its own; the first write root is the project folder"),
    el("div", { class: "row", style: "gap:20px;flex-wrap:wrap;align-items:flex-start" },
      el("div", {}, el("span", { class: "faint small" }, "read"), readRoots.node),
      el("div", {}, el("span", { class: "faint small" }, "write"), writeRoots.node)),
    el("div", { class: "row mt" }, saveRoots)));
  // Deliberation: saved to config on release (next reply composes with it) AND, when a
  // reply is live, the current run is re-leveled too — a conversation IS one run, so the
  // durable/live distinction collapses here.
  const delib = deliberationControl(detail.deliberation || "deliberate", {
    onCommit: async (level) => {
      try {
        await api(`/api/conversations/${slug}`, { method: "PATCH",
          body: { deliberation: level } });
        if (isLive() && detail.run_id) {
          await api(`/api/runs/${detail.run_id}/deliberation`,
            { method: "POST", body: { level } }).catch(() => {});
        }
        toast(`deliberation: ${level}`);
      } catch (err) { toast(err.message, 4000, { error: true }); }
    },
  });
  capBody.append(el("div", { class: "row mt", style: "gap:10px;align-items:flex-start" },
    el("span", { class: "faint small", style: "min-width:150px;padding-top:4px" },
      "deliberation — thinking on paper"),
    delib.node));
  capBody.append(permissionsPanel(detail.permissions, detail.capabilities, {
    disableRuns: "a conversation is one continuous run — previous-run depth is routine-only",
    saveLabel: "save permissions",
    onSave: async (payload) => {
      try {
        await api(`/api/conversations/${slug}/permissions`, { method: "PUT", body: payload });
        toast("permissions saved — they apply from the next reply");
      } catch (err) { toast(err.message, 4000, { error: true }); }
    },
  }).node);
  // Connections: bind an OAuth account per provider so connector utils (google-api, notion…)
  // get a live access token — the same card routines use. D55 closes R70: a conversation could
  // not bind a Google connection because this surface existed only on routine pages.
  capBody.append(el("div", { class: "faint small mt" }, "connections — bind an OAuth account so "
    + "connector utils get a live token (google-api, notion…)"),
    connectionsCard(detail.connections || {}, {
      onSave: (connections) => api(`/api/conversations/${slug}`,
        { method: "PATCH", body: { connections } }),
    }));
  // General rules: a conversation shifts topic mid-thread, so a newly bound rule is pushed
  // to the reply in flight as well as saved for every reply after it (the server does both).
  const ruleHost = el("div", { class: "mt" });
  const buildRules = async () => {
    const lib = await api("/api/library").catch(() => ({ rules: [] }));
    return rulePicker(lib.rules || [], detail.rules || [], {
      live: isLive(),
      onSave: async (payload) => {
        await api(`/api/conversations/${slug}/rules`, { method: "POST", body: payload });
      },
    }).node;
  };
  capBody.append(el("div", { class: "faint small mt" }, "general rules — shared library prose "
    + "it applies to this thread; binding one applies from the current reply on"), ruleHost);
  buildRules().then((n) => ruleHost.replaceChildren(n));
  caps.append(capBody);
  head.replaceChildren(
    el("div", { class: "conv-head-row" }, stateChip, title,
      el("span", { style: "margin-left:auto" }), del),
    el("div", { class: "conv-head-row sub" }, modelControl(detail, slug, isLive),
      el("span", { class: "conv-tagwrap" }, el("span", { class: "faint small" }, "tags"), tagsRow)),
    caps);
}
