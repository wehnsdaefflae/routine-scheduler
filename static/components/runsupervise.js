// The run page's two controls that act on the RUN rather than on its settings: ask a live run
// for a supervisory rung (F750), and rewind a terminal one to a chosen turn (D69). Both live
// here for the reason `runswitch.js` does: `static/views/run.js` is 599 lines against this
// repo's ~350-line one-responsibility budget, so a control that grows gets its own module.
//
// WHY A REQUEST AND NOT A SECOND RUN (F750). The operator pressed run-now with the brief "rung"
// twice on 2026-10-16 while `fau` was live and got "routine fau is busy" both times. That
// refusal is correct — the daemon tracks one active run per routine and two would race the
// routine's state — so the supervisory run cannot be a second run of the routine. The
// escalation ladder already supervises a live run safely: in-process, at the worker's next turn
// boundary, as an oversight child whose turns are its own. This button records that request on
// the run; the engine honours it at the next boundary, whatever the routine's ladder knob says,
// and consumes it (one ask, one rung).

//
// F779: both buttons run their action through util.js `act()`, the one place the
// disable/await/toast/re-enable shape is written. They used to hand-roll it, and the helper's
// own docstring names both failures that caused: 👁 supervise re-enabled on BOTH paths only
// because nothing between the await and the re-enable threw — adding a second toast or a
// refresh call would leave the button permanently dead — and ⟲ rewind re-enabled only in its
// `catch`, so a failure it did not throw on left it dead. `act()` re-enables in a `finally`,
// the only way it cannot be forgotten, and it hands focus back, which a disabled button
// could not hold.

import { api } from "/static/api.js";
import { promptDialog } from "/static/components/dialog.js";
import { act, el, toast } from "/static/util.js";

/** createSuperviseButton(runId) → button
 *  Live runs only — the caller hides it on a terminal run, where there is no turn boundary
 *  left to honour the ask at (the endpoint refuses one too, so the two agree). */
export function createSuperviseButton(runId) {
  const btn = el("button", { class: "btn small", "data-supervise": "1",
    title: "ask for a supervisory rung at this run's next turn boundary" }, "👁 supervise");
  btn.onclick = async () => {
    // `act`'s own okMsg is not used: the toast has to carry the endpoint's own wording, which
    // names WHICH boundary the rung was recorded for. A failure is already toasted by `act`.
    const r = await act(btn, () => api(`/api/runs/${runId}/supervise`, { method: "POST" }));
    if (r) toast(r.supervise || "a rung runs at the next turn boundary", 5000);
  };
  return btn;
}

/** createRewindButton(runId, remountSoon) → button
 *  D69: rewind a terminal run to a chosen turn and re-open it live from there — the remedy for
 *  a run that died or derailed (e.g. a context overflow) instead of losing the whole
 *  conversation. Truncates the transcript through the turn (archiving the dropped tail) and
 *  resumes on the same run dir. */
export function createRewindButton(runId, remountSoon) {
  const btn = el("button", { class: "btn small", hidden: true,
    title: "rewind to a chosen turn and continue from there" }, "⟲ rewind");
  btn.onclick = async () => {
    const ans = await promptDialog(
      `Rewind ${runId}: keep the transcript through which turn? Everything after it is `
      + `archived and the run re-opens live from that point.`, { placeholder: "turn number" });
    if (ans == null) return;
    const turn = parseInt(ans, 10);
    if (!Number.isInteger(turn) || turn < 1) {
      toast("enter a turn number (1 or higher)", 4000, { error: true }); return;
    }
    const r = await act(btn, () => api(`/api/runs/${runId}/rewind`,
                                       { method: "POST", body: { turn } }));
    if (r) {
      toast(`rewound to turn ${r.kept_through_turn} — reconnecting…`);
      remountSoon();
    }
  };
  return btn;
}
