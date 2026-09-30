// Routine settings — MODELS: which catalog model runs each role. Behind "more": how much of the
// model's thinking lands on paper (deliberation).

import { el } from "/static/util.js";
import { settingsSection } from "/static/components/settings-section.js";
import { fieldBlock, settingsGroup } from "/static/components/settings-field.js";
import { describe } from "/static/components/settings-digest.js";
import { deliberationControl } from "/static/components/deliberation.js";

// per routine: main / tool_call / uncensored; children run main by default, a spawn/subtask
// call may override per child
const MODEL_KINDS = [["main", "the orchestrator loop (children inherit it by default)"],
                     ["tool_call", "the llm action"],
                     ["uncensored", "a refused llm call is referred here (opt-in)"]];

export function modelsGroup(ctx) {
  const { d, form } = ctx;
  const catalog = d.catalog || [];          // catalog model names (see Settings → Models)
  const sysM = d.system_model;              // the system model's catalog name (or null)

  const models = fieldBlock(form, "models", (value, set) => {
    const current = { ...(value || {}) };
    const rows = MODEL_KINDS.map(([kind, desc]) => {
      const sel = el("select", { "data-model-role": kind, "data-nopersist": true,
        onchange: () => {
          if (sel.value) current[kind] = sel.value; else delete current[kind];
          set({ ...current });
        } },
        el("option", { value: "" }, sysM ? `— system default (${sysM}) —` : "— system default —"),
        ...catalog.map((n) => el("option", { value: n }, n)),
        // a name the catalog no longer has stays visible, so it can be seen and changed
        ...(current[kind] && !catalog.includes(current[kind])
          ? [el("option", { value: current[kind] }, `${current[kind]} — not in the catalog`)] : []));
      sel.value = current[kind] || "";
      return el("div", { class: "row", style: "margin:5px 0" },
        el("span", { class: "ref-tag", style: "min-width:92px;text-align:center" }, kind),
        el("span", { class: "muted small", style: "min-width:150px" }, desc), sel);
    });
    const refMonth = d.spend?.current?.referrals || 0;
    return el("div", {}, ...rows,
      d.referrals_total
        ? el("div", { class: "muted small mt",
            title: "turns or llm calls the main/tool model refused and the uncensored model answered instead (from the durable usage stream)" },
            `↪ uncensored referrals: ${d.referrals_total} total` + (refMonth ? ` · ${refMonth} this month` : ""))
        : null);
  });

  const deliberation = fieldBlock(form, "deliberation", (value, set) =>
    deliberationControl(value || "standard", { onCommit: set }).node);

  return settingsGroup({
    form, title: "Models", hint: "which model runs each role",
    keys: ["models", "deliberation"],
    moreKeys: ["deliberation"],
    digest: () => `deliberation ${describe("deliberation", form.get("deliberation"))}`,
    sections: [
      ...settingsSection({ title: "Models", id: "models" },
        catalog.length
          ? "which catalog model this routine uses for each role — leave on system default to fall back to the system model"
          : "add a model in Settings first",
        models.node),
    ],
    more: [
      ...settingsSection({ title: "Deliberation", id: "deliberation" },
        "how much of the model's thinking lands on paper — the say and notes every action "
        + "carries. A live run is re-levelled from its own page.",
        deliberation.node),
    ],
  });
}
