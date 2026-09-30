// Routine settings — GOAL: when this routine is finished for good and what one finished run
// delivers. Open on arrival.
//
// The two halves have two owners and the page keeps them apart. The FINISH LINE is a setting —
// the operator's, edited in the draft and saved by the page's one accept. WHAT A FINISHED RUN
// DELIVERS is the recipe's `## Done when`, read-only here beside the verdicts the last runs gave
// each line; it changes where the recipe changes. What a run must never do is neither: it is
// guarded before the action, by permissions, general rules and reminders.

import { api } from "/static/api.js";
import { TERMINAL } from "/static/states.js";
import { el } from "/static/util.js";
import { settingsSection } from "/static/components/settings-section.js";
import { fieldBlock, settingsGroup } from "/static/components/settings-field.js";
import { doneWhenPanel, finishLineEditor } from "/static/components/finish-line.js";

export function goalGroup(ctx) {
  const { slug, d, form } = ctx;
  let info = null;                           // GET …/finish-line: what the runs reported
  const doneHost = el("div", {}, el("div", { class: "muted small" }, "reading the recipe…"));

  const finish = fieldBlock(form, "finish_line", (value, set) =>
    finishLineEditor({ value, info, set }), { noWas: true });

  // Revise recipe works from a FINISHED run: the newest one is where the change is asked for.
  const reviseHref = () => {
    const done = (d.runs || []).find((r) => TERMINAL.has(r.state));
    return done ? `#/run/${done.run_id}?revise=1` : "";
  };

  function paintDone() {
    doneHost.replaceChildren(info
      ? doneWhenPanel({ slug, info, reviseHref: reviseHref(), onJump: ctx.jump })
      : el("div", { class: "muted small" }, "the recipe's Done when could not be read"));
  }

  async function reload() {
    try { info = await api(`/api/routines/${slug}/finish-line`); }
    catch { info = null; }
    finish.redraw();
    paintDone();
  }
  reload();
  ctx.onDetail(paintDone);

  const node = settingsGroup({
    form, title: "Goal", hint: "when it is finished · what each run delivers", open: true,
    keys: ["finish_line"],
    sections: [
      ...settingsSection({ title: "Finish line", id: "goal" },
        "when this routine is done for good. Every outcome names who decides it is reached; "
        + "the finish line is reached when all of them are, or when its end date has passed. "
        + "It stops scheduling — it never limits what one run does.",
        finish.node),
      ...settingsSection({ title: "What a finished run delivers", id: "done-when" },
        ["the recipe's own ", el("code", {}, "## Done when"), " — one line per outcome of ONE "
         + "run, beside the verdict each of the last runs gave it. Read-only here: the recipe "
         + "owns it."],
        doneHost),
    ],
  });
  return { node, reload };
}
