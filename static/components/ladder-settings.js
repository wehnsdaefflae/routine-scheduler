// The ESCALATION LADDER's controls — the one feature whose settings a person could previously
// only reach by editing YAML (operator, 2026-10-03: "obviously it needs a complete ui").
//
// It is deliberately TWO controls, because the ladder is split across two authority classes
// (docs/architecture.md) and the split is the point:
//
//   ladderSwitch  — `ladder.enabled` + `ladder.max_depth`, which live in routine.yaml. CONFIG:
//                   authority over being watched is the user's, so it saves through the accept
//                   bar like every other config field.
//   ladderIntervals — `ladder_rung_height` (n) + `oversight_turns`, which live in tuning.yaml.
//                   RECIPE-classed, so a meta-routine may re-level them on measured evidence.
//
// Both are dumb: they paint locally and hand the value to `onCommit`; the consumer decides what
// a commit means. `oversight_turns` left empty is DERIVED (n // 2 + 1, floored at 4) — shown as
// the number the engine will actually use, never as a blank that reads like zero.

import { el } from "/static/util.js";

//: engine/ladder.MIN_OVERSIGHT_TURNS — a shorter interval cannot be judged, and a rung given
//: fewer turns than this hands back nothing, which reads as `continue`.
const MIN_TURNS = 4;

/** n // 2 + 1, floored — engine/ladder.oversight_turns_for, so the hint cannot drift from it. */
export function derivedTurns(height) {
  return Math.max(Math.floor(height / 2) + 1, MIN_TURNS);
}

/**
 * `ladder.enabled` + `ladder.max_depth` (config). `value` is the stored block; `onCommit`
 * receives the whole merged block, since the endpoint validates it merged.
 * `depthCeiling` is budgets.max_subrun_depth, which floors the depth at run time — named here
 * so a setting that cannot take effect says so where it is set.
 */
export function ladderSwitch(value, { onCommit, depthCeiling } = {}) {
  const cur = { enabled: false, max_depth: 3, ...(value || {}) };
  const commit = () => onCommit?.({ ...cur });

  const onoff = el("input", { type: "checkbox", "data-ladder-enabled": true,
                              "data-nopersist": true });
  onoff.checked = !!cur.enabled;
  const depth = el("input", { type: "number", min: "1", max: "9", step: "1",
                              style: "width:64px", "data-ladder-depth": true,
                              "data-nopersist": true, value: String(cur.max_depth) });
  const state = el("span", { class: "muted small" });
  const ceiling = el("div", { class: "muted small" });

  const paint = () => {
    state.textContent = cur.enabled
      ? `on — a rung reads this routine's runs and may redirect them`
      : "off — no run of this routine is supervised";
    depth.disabled = !cur.enabled;
    // A depth the tree's own ceiling already caps is a setting with no effect: say it here,
    // where it is being chosen, rather than leaving it to be discovered in a transcript.
    const capped = Number.isInteger(depthCeiling) && depthCeiling < cur.max_depth;
    ceiling.textContent = capped
      ? `⚠ budgets.max_subrun_depth is ${depthCeiling}, so the ladder stops at rung `
        + `${depthCeiling} whatever this says`
      : "";
  };
  onoff.onchange = () => { cur.enabled = onoff.checked; paint(); commit(); };
  depth.onchange = () => {
    const n = parseInt(depth.value, 10);
    if (!Number.isInteger(n) || n < 1) { depth.value = String(cur.max_depth); return; }
    cur.max_depth = n;
    paint();
    commit();
  };
  paint();

  return {
    node: el("div", { class: "ladder-set" },
      el("label", { class: "row", style: "gap:8px;align-items:center" }, onoff,
        el("span", {}, "supervise runs of this routine"), state),
      el("div", { class: "row mt", style: "gap:8px;align-items:center" },
        el("span", { class: "muted small", style: "min-width:92px" }, "max rungs"), depth,
        el("span", { class: "muted small" },
          "the top rung escalates to a PERSON, never to a further rung")),
      ceiling),
    get value() { return { ...cur }; },
  };
}

/**
 * `ladder_rung_height` + `oversight_turns` (tuning). `onCommit` receives one `{key: value}` at
 * a time, because the two land in tuning.yaml independently; `oversight_turns` commits `null`
 * when cleared, which is how the engine is told to derive it again.
 */
export function ladderIntervals({ height, turns } = {}, { onCommit } = {}) {
  let n = Number.isInteger(height) ? height : 20;
  const heightIn = el("input", { type: "number", min: String(MIN_TURNS), max: "200", step: "1",
                                 style: "width:110px", "data-ladder-height": true,
                                 "data-nopersist": true, value: String(n) });
  // Wide enough for its PLACEHOLDER, not just its value: at 72px "derived" rendered as "deri",
  // which reads like a typo'd entry rather than an empty field with a derived default — seen in
  // the rendered page, invisible in the source.
  const turnsIn = el("input", { type: "number", min: String(MIN_TURNS), max: "200", step: "1",
                                style: "width:110px", placeholder: "auto",
                                "data-ladder-turns": true, "data-nopersist": true,
                                value: Number.isInteger(turns) ? String(turns) : "" });
  const turnsHint = el("div", { class: "muted small" });
  const heightHint = el("div", { class: "muted small" });

  const paint = () => {
    heightHint.textContent = `at most one rung every ${n} turns — a CEILING, not a metronome: `
      + "a repeated failure, an outcome claimed met, or the run asking pulls one forward";
    turnsHint.textContent = turnsIn.value
      ? `a rung gets ${turnsIn.value} turns of its own — the worker's budget is untouched`
      : `empty = derived from the interval: ${derivedTurns(n)} turns (n ÷ 2 + 1, floor ${MIN_TURNS})`;
  };
  heightIn.onchange = () => {
    const v = parseInt(heightIn.value, 10);
    if (!Number.isInteger(v) || v < MIN_TURNS) { heightIn.value = String(n); return; }
    n = v;
    paint();
    onCommit?.({ ladder_rung_height: n });
  };
  turnsIn.oninput = paint;
  turnsIn.onchange = () => {
    // 0, not null: the routine PATCH dumps with `exclude_none`, under which a null reads as
    // "not sent" and the knob would silently keep its old pinned value. 0 is the API's one
    // spelling of "derive it again" and is written as a removal from tuning.yaml.
    if (!turnsIn.value.trim()) { paint(); onCommit?.({ oversight_turns: 0 }); return; }
    const v = parseInt(turnsIn.value, 10);
    if (!Number.isInteger(v) || v < MIN_TURNS) { turnsIn.value = ""; paint(); return; }
    paint();
    onCommit?.({ oversight_turns: v });
  };
  paint();

  return {
    node: el("div", { class: "ladder-set" },
      el("div", { class: "row", style: "gap:8px;align-items:center" },
        el("span", { class: "muted small", style: "min-width:92px" }, "rung height"), heightIn,
        el("span", { class: "muted small" }, "turns")),
      heightHint,
      el("div", { class: "row mt", style: "gap:8px;align-items:center" },
        el("span", { class: "muted small", style: "min-width:92px" }, "rung budget"), turnsIn,
        el("span", { class: "muted small" }, "turns")),
      turnsHint),
    get value() {
      const v = parseInt(turnsIn.value, 10);
      // 0 = derive it (see onchange): the same spelling the API takes.
      return { ladder_rung_height: n, oversight_turns: Number.isInteger(v) ? v : 0 };
    },
  };
}
