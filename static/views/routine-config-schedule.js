// Routine config — WHEN it fires: the cron-like schedule, the improvement opt-in, the run
// gate (D141), event triggers, and the one-shot future run.
//
// Split out of routine-config.js along routine.js's SECTION_GROUPS; this module is the
// "Schedule & triggers" group. It also owns `refreshHead` — the in-place header refresher —
// because the next-fire line it repaints is built here, and a save in this group is the one
// thing on the page that moves it. renderConfigSections hands it back to routine.js, whose
// run-lifecycle bus handler calls it.

import { api } from "/static/api.js";
import { el, toast, toastError, when } from "/static/util.js";
import { settingsSection } from "/static/components/settings-section.js";
import { scheduleEditor } from "/static/components/schedule.js";
import { scheduleOnceCard } from "/static/components/schedule-once.js";
import { triggersCard } from "/static/components/triggers.js";

export function scheduleSections(view, d, { slug, chipHost, runChip, refreshSurface }) {
  // -- schedule -------------------------------------------------------------------
  const nextFireLine = el("div", { class: "muted mt small" },
    ...(d.next_fire ? ["next run · ", when(d.next_fire)] : []));
  // saves update the header chip + next-fire IN PLACE — never a page reload
  async function refreshHead() {
    try {
      const nd = await api(`/api/routines/${slug}`);
      chipHost.replaceChildren(runChip(nd));
      nextFireLine.replaceChildren(...(nd.next_fire ? ["next run · ", when(nd.next_fire)] : []));
    } catch { /* cosmetic refresh — the save itself already succeeded */ }
  }
  // D71: a member of a SCHEDULED lane is "lane managed" — the dropdown locks on that
  // state (linking to the lane) and a save leaves the stored schedule untouched.
  //
  // With ONE exception, which is why the editor takes a save of its own here: a lane-managed
  // routine's file can still record a cron the daemon suppresses. Clearing it decides nothing
  // about timing — the lane's clock is untouched and the routine fires exactly as before — so
  // it is not the lane's call to make; the surface's `schedule:cron` row sends the reader here
  // to make it. A manual spec is what "no cron of its own" is stored as.
  const sched = scheduleEditor(d.schedule_friendly || { frequency: "manual" }, d.server_tz,
    { catchup: d.catchup || "skip", laneManaged: d.lane_managed || null, allowDisabled: true,
      onClearCron: async () => {
        try {
          await api(`/api/routines/${slug}`, { method: "PATCH",
            body: { schedule: { friendly: { frequency: "manual" } } } });
          toast("stored cron cleared — this routine goes on firing with its lane");
          refreshHead();
          refreshSurface();   // the `schedule:cron` row that sent the reader here is now closed
        } catch (err) {
          // the editor keeps the button live on a rejection, so the reader can try again
          toastError(err);
          throw err;
        }
      } });
  const improveBox = el("input", { type: "checkbox", checked: d.improve !== false || null });
  // D141 (operator, 2026-09-21: "On the config page beside the schedule"). A run gate is a
  // script the daemon runs BEFORE the engine starts: if it reports no work, the run is skipped
  // having spent no tokens at all. That makes it part of WHEN this routine runs, not a separate
  // feature — so it lives in this section and rides its one save button rather than growing a
  // second one. Until now it could only be enabled by hand-editing routine.yaml, which meant a
  // routine could be silently gated off with nothing in the console saying so.
  const gate = d.run_gate || { enabled: false, timeout_s: 30 };
  const gateBox = el("input", { type: "checkbox", "data-run-gate": "",
    checked: gate.enabled || null });
  const gateTimeout = el("input", { type: "number", min: "1", max: "300",
    "data-run-gate-timeout": "", value: String(gate.timeout_s ?? 30), style: "width:76px" });
  const gateTimeoutRow = el("label", { class: "row", style: "gap:6px;align-items:center" },
    el("span", { class: "faint small" }, "give it up to"), gateTimeout,
    el("span", { class: "faint small" }, "seconds to answer"));
  const paintGate = () => { gateTimeoutRow.hidden = !gateBox.checked; };
  gateBox.onchange = paintGate;
  paintGate();
  const gateRow = el("div", { class: "mt" },
    el("label", { class: "row", style: "gap:8px" }, gateBox,
      el("span", {}, "run gate — only start a run when ", el("code", {}, "scripts/run_gate.py"),
        " says there is work")),
    el("div", { class: "faint small", style: "margin:2px 0 0 26px" },
      "the daemon runs that script before the engine starts, so a skipped run costs nothing. ",
      "If the gate errors or misses its deadline the run does not start either — it is recorded ",
      "as failed, so a broken gate stops the routine until you fix it."),
    el("div", { style: "margin:6px 0 0 26px" }, gateTimeoutRow));
  view.append(...settingsSection({ title: "Schedule", id: "schedule" },
    "when this routine runs on its own — a cron-like cadence in the server's timezone, plus the "
    + "Disabled choice that prevents all new starts. Existing runs are unchanged. A routine in a "
    + "scheduled lane follows the lane's clock; it can still be disabled here without changing "
    + "that clock. Manual allows explicit and triggered starts without a recurring cadence. "
    + "The run gate below decides whether a scheduled fire becomes a run at all.",
      sched.node,
      el("label", { class: "row mt", style: "gap:8px" }, improveBox,
        el("span", {}, "include in improvement — the routine-improver meta routine visits this routine (on by default)")),
      gateRow,
      el("div", { class: "row mt" }, el("button", {
        class: "btn primary",
        onclick: async () => {
          try {
            await api(`/api/routines/${slug}`, { method: "PATCH",
              body: { improve: improveBox.checked,
                      run_gate: { enabled: gateBox.checked,
                                  timeout_s: Number(gateTimeout.value) || 30 },
                      schedule: d.lane_managed
                        ? { disabled: sched.value().frequency === "disabled" }
                        : { friendly: sched.value(), catchup: sched.catchup() } } });
            // a cadence moves `schedule:none`; the enable switch takes every schedule row
            // with it, because a routine switched off already says it does not run
            toast("schedule saved"); refreshHead(); refreshSurface();
          } catch (err) { toastError(err); }
        },
      }, "save schedule")),
      nextFireLine));

  // -- triggers: event-driven fires alongside cron (webhook URLs, coalescing) -------
  // ONE intro per section: the mounted card owns it, because it renders wherever the card is
  // mounted (this page and the conversation composer both show these two). The section-level
  // copy said the same thing in different words directly above it.
  view.append(...settingsSection({ title: "Triggers", id: "triggers" }, null,
    triggersCard(slug, d.triggers || [])));

  // -- schedule once: a one-shot future run that fires once then auto-removes --------
  view.append(...settingsSection({ title: "Schedule once", id: "schedule-once" }, null,
    scheduleOnceCard(slug)));

  return { refreshHead };
}
