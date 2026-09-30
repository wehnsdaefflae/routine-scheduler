// Routine settings — SCHEDULE & GATE: when this routine fires and whether a scheduled fire
// becomes a run at all.
//
// Open on arrival, with the Goal group: when it runs and when it is finished are the two
// questions most visits come with. Behind its "more" menu: triggers (event-driven fires), the
// one-shot future run, and the improvement opt-in.

import { api } from "/static/api.js";
import { el, toast, toastError, when } from "/static/util.js";
import { settingsSection } from "/static/components/settings-section.js";
import { fieldBlock, settingsGroup } from "/static/components/settings-field.js";
import { describe } from "/static/components/settings-digest.js";
import { gateEditor } from "/static/components/gate-editor.js";
import { scheduleEditor } from "/static/components/schedule.js";
import { scheduleOnceCard } from "/static/components/schedule-once.js";
import { triggersEditor } from "/static/components/triggers.js";

export function scheduleGroup(ctx) {
  const { slug, d, form } = ctx;
  const nextFireLine = el("div", { class: "muted mt small", "data-next-fire": "" });
  const paintNext = () => nextFireLine.replaceChildren(
    ...(d.next_fire ? ["next run · ", when(d.next_fire)] : []));
  paintNext();
  ctx.onHead(paintNext);

  // D71: a member of a SCHEDULED lane is "lane managed" — the lane's clock decides when it
  // fires, so the cadence select locks on that state (Disabled stays: switching the routine off
  // does not touch the lane). The one schedule edit left to it is an ACTION, not a setting:
  // clearing a cron the lane already suppresses changes no firing, so it saves on its own button
  // (the surface's `schedule:cron` row sends the reader there) and the draft re-reads after it.
  const schedule = fieldBlock(form, "schedule", (value, set) => {
    const spec = value || {};
    return scheduleEditor(spec.friendly || { frequency: "manual" }, d.server_tz, {
      catchup: spec.catchup || "skip", laneManaged: d.lane_managed || null, allowDisabled: true,
      onChange: set,
      onClearCron: async () => {
        try {
          await api(`/api/routines/${slug}`, { method: "PATCH",
            body: { schedule: { friendly: { frequency: "manual" } } } });
          toast("stored cron cleared — this routine goes on firing with its lane");
          await ctx.reloadSettings();
          ctx.refreshHead();
          ctx.refreshSurface();   // the `schedule:cron` row that sent the reader here is closed
        } catch (err) {
          toastError(err);
          throw err;              // the editor keeps its button live for another try
        }
      } }).node;
  });

  const gate = fieldBlock(form, "run_gate", (value, set) =>
    gateEditor({ slug, value, onChange: set }).node);

  const triggers = fieldBlock(form, "triggers", (value, set) =>
    triggersEditor({ value, described: d.triggers || [], set }));
  ctx.onDetail(() => triggers.redraw());   // an accepted webhook has its URL now

  const improve = fieldBlock(form, "improve", (value, set) => {
    const box = el("input", { type: "checkbox", checked: value !== false ? "" : null,
      "data-improve": "", onchange: () => set(box.checked) });
    return el("label", { class: "row", style: "gap:8px" }, box,
      el("span", {}, "include in improvement — the routine-improver meta routine visits this "
        + "routine and may refine its recipe"));
  });

  const node = settingsGroup({
    form, title: "Schedule & gate", hint: "when it fires · whether a fire becomes a run",
    open: true,
    keys: ["schedule", "run_gate", "triggers", "improve"],
    moreKeys: ["triggers", "improve"],
    digest: () => [describe("triggers", form.get("triggers")),
                   describe("improve", form.get("improve"))].join(" · "),
    sections: [
      ...settingsSection({ title: "Schedule", id: "schedule" },
        "when this routine runs on its own — a cron-like cadence in the server's timezone, plus "
        + "the Disabled choice that prevents all new starts (existing runs are unchanged). "
        + "Manual allows explicit and triggered starts without a recurring cadence. A routine in "
        + "a scheduled lane follows the lane's clock; it can still be disabled here without "
        + "changing that clock.",
        schedule.node, nextFireLine),
      ...settingsSection({ title: "Run gate", id: "run-gate" },
        ["whether a scheduled fire becomes a run at all. The gate asks its CHECKS before the "
         + "engine starts — each one a known question with parameters (is there unread mail from "
         + "these senders? did this page change? is a dated duty due?) — and a fire is skipped, "
         + "at no cost, only when every check knows there is nothing to do. Add a check with ",
         el("b", {}, "+ add a check…"), "; ",
         el("b", {}, "Test the gate now"), " asks the checks as shown, saved or not, without "
         + "starting a run. The routine's own predicate is one more check, ",
         el("code", {}, "script"), ": its file is ", el("code", {}, "scripts/admit.py"),
         ", edited below that check's row and saved at once, because it is a file of the routine "
         + "rather than a setting. The gate applies to every scheduled fire — its own cadence, a "
         + "lane's clock, a boot catch-up — and never to Run now, a trigger, a one-shot or a resume."],
        gate.node),
    ],
    more: [
      ...settingsSection({ title: "Triggers", id: "triggers" }, null, triggers.node),
      ...settingsSection({ title: "Schedule once", id: "schedule-once" },
        "an action, not a setting: arming a one-shot takes effect at once.",
        scheduleOnceCard(slug)),
      ...settingsSection({ title: "Include in improvement", id: "improve" }, null, improve.node),
    ],
  });
  return node;
}
