// The conversation header: editable title, tags, delete, and the capabilities panel
// (budgets, deliberation, permissions, practice modules) + the model switcher - split
// from conversations.js. onListChanged refreshes the sidebar after title/tag edits.

import { api } from "/static/api.js";
import { deliberationControl } from "/static/components/deliberation.js";
import { confirmDialog } from "/static/components/dialog.js";
import { permissionsPanel } from "/static/components/permissions.js";
import { tagsEditor } from "/static/components/tags.js";
import { traitPicker } from "/static/components/traitpicker.js";
import { navigate } from "/static/router.js";
import { el, toast } from "/static/util.js";

// The model line at the top of a conversation: shows the EFFECTIVE model (override or
// system default) and switches it at any point — routine.yaml is patched (each reply
// boots on it), and a live reply additionally gets the mid-run control.json switch.
function modelControl(detail, slug, isLive) {
  const cur = detail.models?.main || "";        // a catalog model NAME, or "" = system default
  const sysLabel = detail.system_model || "system model";
  const sel = el("select", { style: "width:auto;font-size:11.5px;padding:3px 6px" },
    el("option", { value: "" }, `default · ${sysLabel}`),
    (detail.catalog || []).map((n) =>
      el("option", { value: n, selected: cur === n || null }, n)));
  const apply = el("button", { class: "btn small primary", hidden: true }, "apply");
  sel.onchange = () => { apply.hidden = false; };
  apply.onclick = async () => {
    const name = sel.value;
    const models = name ? { main: name, subroutine: name, tool_call: name } : {};
    try {
      await api(`/api/conversations/${slug}`, { method: "PATCH", body: { models } });
      if (name && isLive() && detail.run_id) {
        // the current reply switches its main too, at its next turn boundary
        await api(`/api/runs/${detail.run_id}/model`,
          { method: "POST", body: { model: name } }).catch(() => {});
      }
      toast(name ? `model → ${name}` : `model → ${sysLabel}`);
      apply.hidden = true;
    } catch (err) { toast(err.message, 4000, { error: true }); }
  };
  return el("span", { class: "conv-model" },
    el("span", { class: "faint small" }, "model"), sel, apply);
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
  // greyed) + traits read-only
  const caps = el("details", { class: "small conv-caps" },
    el("summary", { style: "cursor:pointer;color:var(--muted)" },
      `⚙ capabilities & budgets${detail.workdir ? ` · project: ${detail.workdir}` : ""}`));
  const capBody = el("div", { class: "conv-opts" });
  const b = detail.budgets || {};
  const numIn = (v, min = "1") => el("input", { type: "number", min, value: v,
    style: "width:90px;font-size:11.5px;padding:3px 6px" });
  const turnsIn = numIn(b.max_turns ?? 10);
  const minsIn = numIn(b.max_wall_clock_min ?? 30, "-1");    // -1 = unlimited time
  const tokIn = numIn(b.max_total_tokens ?? 400000, "-1");   // -1 = unlimited tokens
  const saveBudgets = el("button", { class: "btn small" }, "save budgets");
  saveBudgets.onclick = async () => {
    try {
      await api(`/api/conversations/${slug}`, { method: "PATCH", body: { budgets: {
        max_turns: +turnsIn.value || 10, max_wall_clock_min: +minsIn.value || 30,
        max_total_tokens: +tokIn.value || 400000 } } });
      toast("budgets saved — they cap EACH reply, from the next one");
    } catch (err) { toast(err.message, 4000, { error: true }); }
  };
  const budgetField = (label, input) => el("label", { style: "flex-direction:column" },
    el("span", { class: "faint" }, label), input);
  capBody.append(el("div", { class: "row", style: "gap:12px;flex-wrap:wrap;align-items:flex-end" },
    budgetField("turns / reply", turnsIn), budgetField("minutes / reply (-1=∞)", minsIn),
    budgetField("tokens / reply (-1=∞)", tokIn), saveBudgets));
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
  // Practice modules: a conversation shifts topic mid-thread, so an addition is pushed to
  // the reply in flight as well as saved for every reply after it (the server does both).
  const traitHost = el("div", { class: "mt" });
  const buildTraits = async () => {
    const lib = await api("/api/library").catch(() => ({ traits: [] }));
    return traitPicker(lib.traits || [], detail.traits || [], {
      live: isLive(),
      onSave: async (payload) => {
        await api(`/api/conversations/${slug}/traits`, { method: "POST", body: payload });
      },
    }).node;
  };
  capBody.append(el("div", { class: "faint small mt" }, "practice modules — its own standing "
    + "practices; an addition applies from the current reply on"), traitHost);
  buildTraits().then((n) => traitHost.replaceChildren(n));
  caps.append(capBody);
  head.replaceChildren(
    el("div", { class: "conv-head-row" }, stateChip, title,
      el("span", { style: "margin-left:auto" }), del),
    el("div", { class: "conv-head-row sub" }, modelControl(detail, slug, isLive),
      el("span", { class: "conv-tagwrap" }, el("span", { class: "faint small" }, "tags"), tagsRow)),
    caps);
}
