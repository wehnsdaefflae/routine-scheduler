// The deliberation slider — how much of the model's thinking lands ON PAPER (the
// persistent say/notes prose channel; a model's `effort` is the ephemeral counterpart).
// Four named stops, not a continuum: each stop is a qualitatively distinct say contract
// (engine/deliberation.py owns the wording the model sees).
//
// Dumb by design: paints on drag, fires onCommit on release; the consumer decides what a
// commit means (PATCH config, POST to a live run, or just read .value at finalize).

import { el } from "/static/util.js";

export const LEVELS = ["terse", "standard", "deliberate", "think-on-paper"];
const HINTS = {
  "terse": "one clause per step — for cheap, mechanical routines",
  "standard": "finding first; 2-3 sentences at decisions",
  "deliberate": "adds the context behind each step, incl. knowledge beyond the run",
  "think-on-paper": "writes deliberation to state/notes.md before decisions (~1 extra turn each)",
};

export function deliberationControl(initial, { onCommit } = {}) {
  const idx = Math.max(0, LEVELS.indexOf(initial));
  const name = el("span", { class: "ref-tag", style: "min-width:104px;text-align:center" });
  const hint = el("div", { class: "muted small", style: "margin-top:3px" });
  const range = el("input", { type: "range", min: "0", max: String(LEVELS.length - 1),
                              step: "1", value: String(idx), style: "width:150px",
                              "data-nopersist": true, title: "deliberation level" });
  const paint = () => {
    const level = LEVELS[+range.value];
    name.textContent = level;
    hint.textContent = HINTS[level];
  };
  range.oninput = paint;
  range.onchange = () => onCommit?.(LEVELS[+range.value]);
  paint();
  return {
    node: el("div", { class: "delib" },
      el("div", { class: "row", style: "gap:9px;align-items:center" }, range, name), hint),
    get value() { return LEVELS[+range.value]; },
    set(level) {
      const i = LEVELS.indexOf(level);
      if (i >= 0) { range.value = String(i); paint(); }
    },
  };
}
