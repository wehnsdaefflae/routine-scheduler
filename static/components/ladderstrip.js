// The rail's OVERSIGHT strip: what the escalation ladder did to this run. Three facts, in the
// order a reader needs them — which rung is current, how many turns until the next escalation,
// and what the last rung ruled (engine/ladder.py's four events, folded by readmodels/ladder.py).
//
// It renders NOTHING and hides its rail section when the run has no ladder record, which is the
// overwhelming majority: a card that is always there and always empty teaches a reader to skip
// the one place supervision is reported. A SKIP is shown as loudly as a verdict — a run with the
// ladder on and no supervision in it is the failure the reason explains (no supervisor pattern,
// the tree's ceiling, a spent budget), and it is otherwise indistinguishable from a healthy run
// nobody needed to redirect.

import { el } from "/static/util.js";

const VERDICT_CLASS = { on_track: "ok", off_track: "warn", stuck: "bad", failing: "bad" };

/** The strip's line for what the last rung did — its own sentence, never a bare enum. */
function lastLine(st) {
  if (st.last === "skipped") {
    return ["warn", `rung ${st.rung || "?"} did not run — ${st.last_reason || "no reason recorded"}`];
  }
  if (st.last === "silent") {
    return ["warn", `rung ${st.rung || "?"} handed back no directive`
      + (st.last_reason ? ` (${st.last_reason})` : "")];
  }
  if (st.last === "dispatched") {
    return ["", `rung ${st.rung || "?"} is reading the run now`];
  }
  const cls = VERDICT_CLASS[st.verdict] || "";
  return [cls, `rung ${st.rung || "?"}: ${st.verdict || "?"} · ${st.disposition || "?"}`];
}

/**
 * Render the ladder strip into `host`. `state` is the run detail's `ladder` field; the caller
 * registers the rail's `oversight` section only for a run that HAS one, so a run with no rung
 * carries no extra card at all. A state that is nevertheless absent hides the section rather
 * than drawing an empty one.
 */
export function renderLadder(host, rail, state) {
  if (!state || typeof state !== "object") {
    rail?.toggle?.("oversight", false);
    return;
  }
  const [cls, last] = lastLine(state);
  const rows = [el("div", { class: `ls-last ${cls}` }, last)];
  if (state.turns_to_next !== null && state.turns_to_next !== undefined) {
    rows.push(el("div", { class: "faint small" },
      `next escalation in ${state.turns_to_next} turn${state.turns_to_next === 1 ? "" : "s"}`));
  }
  if (state.next_look) {
    rows.push(el("div", { class: "faint small" }, `next look: ${state.next_look}`));
  }
  // The counts say whether supervision actually happened: a run with three dispatches reads
  // differently from one with three skips, and both show "rung 3" on the line above.
  const counts = [];
  if (state.dispatched) counts.push(`${state.dispatched} dispatched`);
  if (state.skipped) counts.push(`${state.skipped} skipped`);
  if (counts.length) rows.push(el("div", { class: "faint small" }, counts.join(" · ")));
  host.replaceChildren(el("div", { class: "ladderstrip", "data-ladder": state.last || "" },
    ...rows));
}
