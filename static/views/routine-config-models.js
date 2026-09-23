// Routine config — MODELS & RESOURCES: which catalog model runs each role, how much
// deliberation lands on paper, output compression, the OAuth connections its util calls act
// as, and the machines it may reach.
//
// Split out of routine-config.js along routine.js's SECTION_GROUPS; this module is the
// "Models & resources" group. Connections and machines are RESOURCE bindings, not
// permissions — which is why they sit beside the model roles rather than in the abilities
// panel: they say what is in reach, the ability says what may be done with it.

import { api } from "/static/api.js";
import { act, el, toast, toastError } from "/static/util.js";
import { settingsSection } from "/static/components/settings-section.js";
import { connectionsCard } from "/static/components/connections.js";
import { deliberationControl } from "/static/components/deliberation.js";
import { machinesCard } from "/static/components/machines.js";
import { outputCompression } from "/static/components/output-compression.js";

export function modelSections(view, d, { slug, refreshSurface }) {
  // -- models (per routine: main / tool_call / uncensored; children run main by default,
  //    a spawn/subtask call may override per child) ------------------------------
  const MODEL_KINDS = [["main", "the orchestrator loop (children inherit it by default)"],
                       ["tool_call", "the llm action"],
                       ["uncensored", "a refused llm call is referred here (opt-in)"]];
  const catalog = d.catalog || [];      // catalog model names (see Settings → Models)
  const sysM = d.system_model;          // the system model's catalog name (or null)
  const modelSelects = {};
  const modelRows = MODEL_KINDS.map(([kind, desc]) => {
    const cur = (d.models && d.models[kind]) || "";   // a catalog model NAME, or "" = fallback
    const sel = el("select", {}, [
      el("option", { value: "" }, sysM ? `— system default (${sysM}) —` : "— system default —"),
      ...catalog.map((n) => el("option", { value: n }, n))]);
    sel.value = cur || "";
    modelSelects[kind] = sel;
    return el("div", { class: "row", style: "margin:5px 0" },
      el("span", { class: "ref-tag", style: "min-width:92px;text-align:center" }, kind),
      el("span", { class: "muted small", style: "min-width:150px" }, desc),
      sel);
  });
  const refMonth = d.spend?.current?.referrals || 0;
  // Deliberation: how much thinking lands on paper (the say/notes contract). Saved on
  // release — the next run composes with the new level (a LIVE run is re-leveled from
  // the run view, control.json-scoped).
  const delib = deliberationControl(d.deliberation || "standard", {
    onCommit: async (level) => {
      try { await api(`/api/routines/${slug}`, { method: "PATCH", body: { deliberation: level } });
        toast(`deliberation: ${level} — applies from the next run`); }
      catch (err) { toastError(err); }
    },
  });
  view.append(...settingsSection({ title: "Models", id: "models" },
    catalog.length
      ? "which catalog model this routine uses for each role — leave on system default to fall back to the system model"
      : "add a model in Settings first",
      ...modelRows,
      outputCompression(d.output_compression, `/api/routines/${slug}`),
      el("div", { class: "row mt", style: "align-items:flex-start" },
        el("span", { class: "ref-tag", style: "min-width:92px;text-align:center" }, "deliberation"),
        el("span", { class: "muted small", style: "min-width:150px" },
          "how much thinking lands on paper"),
        delib.node),
      d.referrals_total
        ? el("div", { class: "muted small mt",
            title: "turns or llm calls the main/tool model refused and the uncensored model answered instead (from the durable usage stream)" },
            `↪ uncensored referrals: ${d.referrals_total} total` + (refMonth ? ` · ${refMonth} this month` : ""))
        : null,
      el("div", { class: "row mt" }, el("button", { class: "btn primary",
        onclick: (e) => {
          const models = {};
          for (const [kind, sel] of Object.entries(modelSelects))
            if (sel.value) models[kind] = sel.value;
          act(e.currentTarget,
              () => api(`/api/routines/${slug}`, { method: "PATCH", body: { models } }),
              "models saved");
        } }, "save models"))));

  // -- connections: bind an OAuth account per provider (Settings → Connections) --------
  // Shared card (components/connections.js) — the conversation header uses the same one.
  // ONE intro per section: the mounted card owns it, because it renders wherever the card is
  // mounted (this page and the conversation composer both show these two). The section-level
  // copy said the same thing in different words directly above it.
  view.append(...settingsSection({ title: "Connections", id: "connections" }, null,
    connectionsCard(d.connections || {}, {
      onSave: async (connections) => {
        await api(`/api/routines/${slug}`, { method: "PATCH", body: { connections } });
        refreshSurface();   // a `connection:` row a held rule expects is bound or unbound here
      },
    })));


  // -- machines: the shared binding card (components/machines.js) — D102: the conversation
  // header mounts the same card, so both surfaces bind catalog machines identically --------
  // ONE intro per section: the mounted card owns it, because it renders wherever the card is
  // mounted (this page and the conversation composer both show these two). The section-level
  // copy said the same thing in different words directly above it.
  view.append(...settingsSection({ title: "Machines", id: "machines" }, null,
    machinesCard(d.machine_catalog || [], d.machines || [], {
      onSave: async (machines) => {
        await api(`/api/routines/${slug}`, { method: "PATCH", body: { machines } });
        refreshSurface();   // a `machine:` row a held doc expects is bound or unbound here
      },
    })));

}
