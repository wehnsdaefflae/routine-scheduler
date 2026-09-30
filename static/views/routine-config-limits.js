// Routine settings — LIMITS & REACH: the per-run ceilings and what the run can reach beyond its
// own directory. Behind "more": how many runs are kept, the accounts its util calls act as, and
// the machines it may reach.
//
// Budgets are a runaway BACKSTOP, never a pace: what decides that a job is finished is the Goal
// group above. Roots, connections and machines are RESOURCE bindings, not permissions — they say
// what is in reach; an ability in the Abilities group says what may be done with it.

import { el, toast } from "/static/util.js";
import { settingsSection } from "/static/components/settings-section.js";
import { fieldBlock, settingsGroup } from "/static/components/settings-field.js";
import { describe } from "/static/components/settings-digest.js";
import { BUDGET_FIELDS, UNLIMITED_BUDGETS } from "/static/components/budgetfields.js";
import { connectionsCard } from "/static/components/connections.js";
import { rootsEditor } from "/static/components/fsroots.js";
import { machinesCard } from "/static/components/machines.js";

/** A number input that reports only a value it can stand behind; anything else snaps back. */
function numberInput(value, { min, onValue, reject, attrs = {} }) {
  const input = el("input", { type: "number", min: String(min), value: String(value ?? ""),
    style: "width:110px", "data-nopersist": true, ...attrs,
    onchange: () => {
      const v = parseInt(input.value, 10);
      const why = reject(v);
      if (why) {
        input.value = String(value ?? "");
        toast(why, 4000, { error: true });
        return;
      }
      value = v;
      onValue(v);
    } });
  return input;
}

export function limitsGroup(ctx) {
  const { d, form } = ctx;

  const budgets = fieldBlock(form, "budgets", (value, set) => {
    const current = { ...(value || {}) };
    return el("div", { "data-budgets": "" }, ...BUDGET_FIELDS.map(([key, label, help]) => {
      const unlimited = UNLIMITED_BUDGETS.includes(key);
      return el("div", { class: "row", style: "margin:5px 0" },
        numberInput(current[key], { min: unlimited ? -1 : 0, attrs: { "data-budget": key },
          reject: (v) => (!Number.isFinite(v) || (v < 1 && !(unlimited && v === -1))
            ? `${label}: needs a positive number${unlimited ? " (or -1 = unlimited)" : ""}` : ""),
          onValue: (v) => { current[key] = v; set({ ...current }); } }),
        el("span", { style: "min-width:220px" }, label),
        el("span", { class: "muted small" }, help));
    }));
  });

  const roots = (key, pickTitle, label) => fieldBlock(form, key, (value, set) =>
    rootsEditor(value || [], { pickTitle, onChange: set,
      emptyText: "none — the run only sees its own directory" }).node, { labels: { [key]: label } });
  const readRoots = roots("fs_read_roots", "add a read-only root", "read-only roots");
  const writeRoots = roots("fs_write_roots", "add a read-write root", "read-write roots");

  const retention = fieldBlock(form, "keep_runs", (value, set) =>
    el("div", { class: "row" },
      numberInput(value ?? 30, { min: 1, attrs: { "data-keep-runs": "" },
        reject: (v) => (!Number.isFinite(v) || v < 1 ? "keep at least 1 run" : ""), onValue: set }),
      el("span", {}, "runs kept")));

  const connections = fieldBlock(form, "connections", (value, set) =>
    connectionsCard(value || {}, { onChange: set }));

  const machines = fieldBlock(form, "machines", (value, set) =>
    machinesCard(d.machine_catalog || [], value || [], { onChange: set }));

  return settingsGroup({
    form, title: "Limits & reach", hint: "per-run ceilings · filesystem reach",
    keys: ["budgets", "fs_read_roots", "fs_write_roots", "keep_runs", "connections", "machines"],
    moreKeys: ["keep_runs", "connections", "machines"],
    digest: () => [describe("keep_runs", form.get("keep_runs")),
                   describe("connections", form.get("connections")),
                   describe("machines", form.get("machines"))].join(" · "),
    sections: [
      ...settingsSection({ title: "Budgets", id: "budgets" },
        ["hard per-run ceilings, checked at every turn — the run is told at 85% so it can wind down ",
         "deliberately. A runaway backstop, not a pace: the Goal group says when a job is done."],
        budgets.node),
      ...settingsSection({ title: "Filesystem roots", id: "fs-roots" },
        ["directories this routine may reach beyond its own — browse to each. A read-only root can ",
         "be read; a read-write root can be read AND written. Every util subprocess is jailed to ",
         "these roots intersected with what the util itself declares, so a path missing here is a ",
         "path the run cannot reach at all. ",
         el("strong", {}, "Read-write roots are powerful"), ": one that covers this routine's own ",
         "directory unlocks editing its OWN recipe (main.md / stages / tuning.yaml) — the same ",
         "lever the routine-improver holds. routine.yaml stays sealed regardless. Takes effect ",
         "at the next run."],
        el("div", { class: "field" }, el("span", {}, "read-only roots"), readRoots.node),
        el("div", { class: "field mt" }, el("span", {}, "read-write roots"), writeRoots.node)),
    ],
    more: [
      ...settingsSection({ title: "Retention", id: "retention" },
        ["how many finished run directories to keep — older ones are pruned (transcripts gzip ",
         "first). The durable usage stream (spend, health) survives pruning."],
        retention.node),
      ...settingsSection({ title: "Connections", id: "connections" }, null, connections.node),
      ...settingsSection({ title: "Machines", id: "machines" }, null, machines.node),
    ],
  });
}
