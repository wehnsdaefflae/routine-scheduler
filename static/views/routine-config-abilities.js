// Routine settings — ABILITIES: what this routine may do (conduct permissions and the
// capabilities they switch on) and how it works (the general rules it practises). Behind "more":
// the library's shared reminders it takes on and the effective surface — the read-only join of
// everything the setup resolves to.
//
// A conduct doc and the capabilities it presumes are ONE decision — ticking a doc switches its
// requirements on, unticking a capability drops the docs that needed it — so the ability cards
// edit the two fields together, as one change.

import { el, skeleton } from "/static/util.js";
import { settingsSection } from "/static/components/settings-section.js";
import { fieldBlock, settingsGroup } from "/static/components/settings-field.js";
import { describe } from "/static/components/settings-digest.js";
import { abilitiesPanel } from "/static/components/abilities.js";
import { remindersPicker } from "/static/components/reminders-picker.js";
import { rulePicker } from "/static/components/rulepicker.js";
import { surfaceView } from "/static/components/surface-view.js";

export function abilitiesGroup(ctx) {
  const { slug, d, form, library } = ctx;

  const abilities = fieldBlock(form, ["permissions", "capabilities"], (value, set) => {
    const held = new Set(value.permissions || []);
    const docs = (d.permissions || []).map((p) => ({ ...p, active: held.has(p.slug) }));
    return abilitiesPanel(docs, { ...d.capabilities, active: value.capabilities || {} }, {
      surface: d.surface,
      saved: { permissions: form.saved("permissions") || [],
               capabilities: form.saved("capabilities") || {} },
      onChange: (v) => set({ permissions: v.active, capabilities: v.capabilities }),
    }).node;
  }, { labels: { permissions: "conduct permissions", capabilities: "capabilities" }, noWas: true });
  // the cards hang each resolved need under the ability that owns it: a fresh surface or a
  // fresh conduct-doc catalogue is a fresh set of cards
  ctx.onDetail(() => abilities.redraw());
  ctx.onSurface(() => abilities.redraw());

  const rules = fieldBlock(form, "rules", (value, set) => {
    const host = el("div", {}, skeleton(["60%", "80%"]));
    library.then((lib) => host.replaceChildren(rulePicker(lib.rules || [], value || [], {
      saved: form.saved("rules") || [], onChange: set }).node));
    return host;
  }, { noWas: true });

  const reminders = fieldBlock(form, "reminders", (value, set) => {
    const host = el("div", {}, skeleton(["60%"]));
    library.then((lib) => host.replaceChildren(
      remindersPicker({ available: lib.reminders || [], value: value || [], set })));
    return host;
  });

  const surfaceHost = el("div", {});
  ctx.setSurfacePanel(surfaceView(surfaceHost, slug, d.surface));

  return settingsGroup({
    form, title: "Abilities", hint: "what it may do · how it works",
    keys: ["permissions", "capabilities", "rules", "reminders"],
    moreKeys: ["reminders"],
    digest: () => `${describe("reminders", form.get("reminders"))} · the effective surface`,
    sections: [
      ...settingsSection({ title: "Permissions & capabilities", id: "permissions" },
        ["what this routine is ALLOWED to do — enforced by the engine on every action. One card per ",
         "ability, carrying everything that ability needs: the action kinds and reserved utils it ",
         "requires, the secrets, roots and bindings it resolves to, plus its POLICY DIAL where it ",
         "has one — how deep it may read previous runs, who approves a util or rule change, which ",
         "reminder stores it writes to. Enforcement reads the capabilities, not the conduct doc, so ",
         "an ability whose requirements are not all switched on fails closed — its card says so, ",
         "with the dial that fixes it inside that card. Only you can change any of this — a routine ",
         "can never grant itself anything. Takes effect at the next run."],
        abilities.node),
      ...settingsSection({ title: "General rules", id: "general-rules" },
        ["the rules this routine reads before the situations they govern. Each states a ",
         "principle the run applies to its own case; the prose lives once in the library, so ",
         "editing it there reaches every routine holding it. Binding one reaches a run already ",
         "in flight, unbinding takes effect at the next run. A run can READ any rule (read_rule) ",
         "but never change this set."],
        rules.node),
    ],
    more: [
      ...settingsSection({ title: "Shared reminders", id: "shared-reminders" },
        ["cautions the library curates, taken on by choice: each HOLDS an action matching its ",
         "pattern before it runs, so the run decides again with the consequence in front of it. ",
         "The routine's own reminders need no list — its runs leave them for themselves; they ",
         "are tallied (and deleted) under Recipe health."],
        reminders.node),
      ...settingsSection({ title: "Effective surface", id: "effective-surface" },
        ["every dependency this routine's setup resolves to — secrets, roots, machines, ",
         "connections, reserved utils — with the conduct doc or util that declares each one. ",
         "Read-only — and read from what the routine HOLDS: a change you have not accepted yet ",
         "shows here once it is. An UNMET row names the remedy and takes you to the control ",
         "that performs it."],
        surfaceHost),
    ],
  });
}
