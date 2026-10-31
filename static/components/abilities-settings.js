// The abilities panel's DIALS: the select every one of them is, and the card holding the four
// that belong to no doc.
//
// Two dials ride a doc's card — write_util's `confirm`, write_rule's `rule_confirm` — because
// each is the approval level of an action that doc requires. The rest are SETTINGS no doc
// switches
// on: how far back a run reads its earlier runs (`runs`), the consequence-reminder layer
// (`reminders`), who approves a write to the library's shared reminders (`remind_confirm`), who
// approves a config change the routine proposes for ITSELF (`config_confirm`, D191 — no
// permission switches it on, because every routine may file a `config_patch`) and
// the job layer (`jobs`: the routine keeps its standing work as jobs, docs/jobs.md).
// The panel carried all three in its state and saved them with every save while showing a
// control for none of them, so a save wrote back values the reader had never been shown. They
// share one card, built in the approval dials' style, and the reminder approval is on it only at
// `global` — the one level that can write a shared reminder, so the only one where the dial
// governs anything. Hiding it changes nothing it holds.

import { el } from "/static/util.js";
import {
  CONFIG_CONFIRM_OPTIONS, JOB_OPTIONS, REMINDER_OPTIONS, REMIND_CONFIRM_OPTIONS,
  REVISE_CONFIRM_OPTIONS, RUNS_OPTIONS,
} from "/static/components/abilities-data.js";

/** One dial: a <select> over `options` resting on `current`. `set` records the choice and
 *  `after` reports it. `key` names the setting (`data-setting`) — a stable hook that survives a
 *  relabelled option — and `label` says what the dial decides, for a reader who cannot see the
 *  row it sits in. */
export function selectDial(options, current, set, { key, label, after }) {
  // An option marked `heldOnly` is never OFFERED (abilities-data.js says which and why). It
  // still SHOWS while that is where the mapping actually stands: a control resting on a value
  // the routine does not hold reads as a closed row, leaving the reader nothing to move.
  const sel = el("select", { "data-setting": key, "aria-label": label },
    ...options.filter(([v, , o]) => !o?.heldOnly || v === current).map(([v, text]) =>
      el("option", { value: v, selected: current === v ? "" : null }, text)));
  sel.onchange = () => { set(sel.value); after(); };
  return sel;
}

/** The settings card. `caps` is the panel's live mapping, `stackRow` its row builder, and
 *  `changed` what a dial reports a change through — the panel's repaint, which is what a
 *  render ends in. The card is built ONCE and never rebuilt by the panel: no doc tick reaches
 *  these values, and rebuilding it would pull a select out from under the keyboard mid-change. */
export function settingsCard({ caps, stackRow, changed }) {
  const row = (kind, key, label, options, after = changed) => stackRow({
    state: "ok", kind, entity: key,
    control: selectDial(options, caps[key], (v) => { caps[key] = v; }, { key, label, after }) });
  // Each approval row NAMES which approval it is. Two rows both reading "approval" sit
  // adjacent whenever reminders are `global` (the reminder one is revealed there), and the
  // only thing telling them apart was help text inside a select that truncates at the card's
  // width — so a reader scanning the kind column met the same word twice. Seen in a render,
  // never by the browser suite: both tests assert behaviour through `data-setting`, which is
  // distinct, so the collision was invisible to them.
  const approval = row("reminder approval", "remind_confirm", "who approves a shared reminder",
                       REMIND_CONFIRM_OPTIONS);
  const reveal = () => { approval.hidden = caps.reminders !== "global"; };
  reveal();
  return el("div", { class: "ability ab-settings", "data-ability-settings": "" },
    el("div", { class: "ability-head" },
      el("span", { "aria-hidden": "true" }, "⚙"),
      el("div", {},
        el("div", { class: "ability-name" }, "Run history, reminders, config & jobs"),
        el("div", { class: "muted small prose" },
          "settings rather than abilities: no conduct doc switches these on, and ticking one "
          + "never moves them"))),
    el("ul", { class: "ability-stack" },
      row("history", "runs", "how far back a run reads its earlier runs", RUNS_OPTIONS),
      row("reminders", "reminders", "the consequence-reminder layer", REMINDER_OPTIONS,
          () => { reveal(); changed(); }),
      approval,
      row("config approval", "config_confirm",
          "who approves a config change it proposes for itself", CONFIG_CONFIRM_OPTIONS),
      row("recipe approval", "revise_confirm",
          "who approves a revision another routine proposes for this recipe",
          REVISE_CONFIRM_OPTIONS),
      row("jobs", "jobs", "the job layer — standing work kept as jobs", JOB_OPTIONS)));
}
