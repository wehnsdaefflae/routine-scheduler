// Routine settings — MODELS: which catalog model runs each role and how hard it reasons
// (effort — a setting of its own, so one catalog model serves every level), and — once the
// instance has decision models — which one answers this routine's decide calls. Behind "more":
// how much of the model's thinking lands on paper (deliberation).

import { el } from "/static/util.js";
import { settingsSection } from "/static/components/settings-section.js";
import { fieldBlock, settingsGroup } from "/static/components/settings-field.js";
import { describe } from "/static/components/settings-digest.js";
import { deliberationControl } from "/static/components/deliberation.js";
import { ladderIntervals } from "/static/components/ladder-settings.js";

// per routine: main / tool_call / uncensored / supervisor; children run main by default, a
// spawn/subtask call may override per child
const MODEL_KINDS = [["main", "the orchestrator loop (children inherit it by default)"],
                     ["tool_call", "the llm action"],
                     ["uncensored", "a refused llm call is referred here (opt-in)"],
                     ["supervisor", "an escalation-ladder rung's overseer (blank = main)"]];
// The decide action's two roles name a DECISION model (Settings → Decision endpoints), not a
// chat one, so their pickers list the decision catalog — the image role only the models that
// take images. A blank role falls back to the instance default for that payload.
const DECISION_ROLES = [["decision", "the decide action"],
                        ["decision_media", "decide calls that carry images"]];

export function modelsGroup(ctx) {
  const { d, form } = ctx;
  const catalog = d.catalog || [];          // catalog model names (see Settings → Models)
  const sysM = d.system_model;              // the system model's catalog name (or null)

  const decisions = d.decision_catalog || [];   // [{name, images}] — empty: no decide action
  const decDefaults = d.decision_defaults || {};

  const levels = d.effort_levels || [];     // the reasoning levels on offer, lowest first
  const sysEffort = d.system_effort;        // what a role with no model of its own runs at

  // One block over BOTH keys: a role's model and its effort are one row and one decision.
  const models = fieldBlock(form, ["models", "efforts"], (value, set) => {
    const current = { ...(value.models || {}) };
    const efforts = { ...(value.efforts || {}) };
    const emit = () => set({ models: { ...current }, efforts: { ...efforts } });
    const commit = (kind, v) => {
      if (v) current[kind] = v; else delete current[kind];
      emit();
    };
    const effortSelect = (kind) => {
      const sel = el("select", { "data-effort-role": kind, "data-nopersist": true,
        "aria-label": `${kind} reasoning effort`, title: "how hard the model reasons on each turn",
        onchange: () => { if (sel.value) efforts[kind] = sel.value; else delete efforts[kind]; emit(); } },
        el("option", { value: "" }, !current[kind] && sysEffort ? `effort: system (${sysEffort})`
          : "effort: provider default"),
        ...levels.map((l) => el("option", { value: l }, l)));
      sel.value = efforts[kind] || "";
      return sel;
    };
    const roleRow = (kind, desc, sel, extra = null) => el("div", { class: "row", style: "margin:5px 0" },
      el("span", { class: "ref-tag", style: "min-width:92px;text-align:center" }, kind),
      el("span", { class: "muted small", style: "min-width:150px" }, desc), sel, extra);
    const decisionRows = !decisions.length ? [] : DECISION_ROLES.map(([kind, desc]) => {
      const offered = decisions.filter((m) => kind !== "decision_media" || m.images);
      // What a blank role means, worded as the engine resolves it (endpoints/decisions.py
      // default_decision_model): an image call tries the routine's decision role first.
      const inst = decDefaults[kind] || (kind === "decision_media" ? decDefaults.decision : null);
      const blank = kind === "decision"
        ? (inst ? `— instance default (${inst}) —` : "— instance default —")
        : `— the decision role if it takes images, else ${inst ? `the instance default (${inst})` : "the instance default"} —`;
      const sel = el("select", { "data-model-role": kind, "data-nopersist": true,
        onchange: () => commit(kind, sel.value) },
        el("option", { value: "" }, blank),
        ...offered.map((m) => el("option", { value: m.name }, m.images ? `${m.name} · text + images` : m.name)),
        ...(current[kind] && !offered.some((m) => m.name === current[kind])
          ? [el("option", { value: current[kind] }, `${current[kind]} — not a decision model here`)] : []));
      sel.value = current[kind] || "";
      return roleRow(kind, desc, sel);
    });
    const rows = MODEL_KINDS.map(([kind, desc]) => {
      const sel = el("select", { "data-model-role": kind, "data-nopersist": true,
        onchange: () => commit(kind, sel.value) },
        el("option", { value: "" }, sysM ? `— system default (${sysM}) —` : "— system default —"),
        ...catalog.map((n) => el("option", { value: n }, n)),
        // a name the catalog no longer has stays visible, so it can be seen and changed
        ...(current[kind] && !catalog.includes(current[kind])
          ? [el("option", { value: current[kind] }, `${current[kind]} — not in the catalog`)] : []));
      sel.value = current[kind] || "";
      return roleRow(kind, desc, sel, effortSelect(kind));
    });
    const refMonth = d.spend?.current?.referrals || 0;
    return el("div", {}, ...rows, ...decisionRows,
      d.referrals_total
        ? el("div", { class: "muted small mt",
            title: "turns or llm calls the main/tool model refused and the uncensored model answered instead (from the durable usage stream)" },
            `↪ uncensored referrals: ${d.referrals_total} total` + (refMonth ? ` · ${refMonth} this month` : ""))
        : null);
  });

  const deliberation = fieldBlock(form, "deliberation", (value, set) =>
    deliberationControl(value || "standard", { onCommit: set }).node);

  // The escalation ladder's TUNING half — how often a rung fires and how many turns it gets.
  // It belongs beside Deliberation because both are tuning.yaml keys (recipe-classed, so a
  // meta-routine may re-level them on measured evidence), unlike the ladder's on/off switch and
  // depth ceiling, which are config and live in Limits & reach → Oversight.
  // One control edits BOTH keys, which is what fieldBlock's list form is for: `value` arrives as
  // {key: value} and `set` takes the same shape, so the two land as one decision.
  const intervals = fieldBlock(form, ["ladder_rung_height", "oversight_turns"], (value, set) =>
    ladderIntervals({ height: value.ladder_rung_height, turns: value.oversight_turns },
                    { onCommit: set }).node,
    { labels: { ladder_rung_height: "rung height", oversight_turns: "rung budget" } });

  return settingsGroup({
    form, title: "Models", hint: "which model runs each role, and how hard it reasons",
    keys: ["models", "efforts", "deliberation", "ladder_rung_height", "oversight_turns"],
    moreKeys: ["deliberation", "ladder_rung_height", "oversight_turns"],
    digest: () => `deliberation ${describe("deliberation", form.get("deliberation"))}`
      + ` · rung every ${form.get("ladder_rung_height") ?? 20} turns`,
    sections: [
      ...settingsSection({ title: "Models", id: "models" },
        catalog.length
          ? "which catalog model this routine uses for each role, and how hard it reasons (effort applies to whichever model serves the role — a failover included) — leave on system default to fall back to the system model"
            + (decisions.length ? "; the decision roles pick from the decision models and fall back to the instance's decision defaults" : "")
          : "add a model in Settings first",
        models.node),
    ],
    more: [
      ...settingsSection({ title: "Deliberation", id: "deliberation" },
        "how much of the model's thinking lands on paper — the say and notes every action "
        + "carries. A live run is re-levelled from its own page.",
        deliberation.node),
      ...settingsSection({ title: "Rung intervals", id: "ladder-intervals" },
        ["how often the escalation ladder fires and what a rung costs. These are TUNING, so a "
         + "meta-routine may re-level them on measured evidence; whether this routine is "
         + "supervised at all is yours, in ",
         el("strong", {}, "Limits & reach → Oversight"),
         ". They change nothing while the ladder is off."],
        intervals.node),
    ],
  });
}
