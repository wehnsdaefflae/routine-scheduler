// What a toggle MEANS — the three lines beside every ability and rule checkbox.
//
// The panels used to label a control with the library doc's title, and the title names a topic:
// "ask policy — when and how to involve the user" tells a reader nothing they can act on
// (operator, 2026-08-30). Nor does the doc BODY, which is written to the RUN in the imperative
// ("read the error before you try again") — an instruction for the agent is not a description
// for the person deciding whether to switch it on.
//
// A toggle is a comparison, so the row states both sides and lets you see the difference:
//
//   on   · what the routine does with it
//   off  · what it does without it
//   hold it when · whether it is for THIS routine, which is the actual decision
//
// The CURRENT side is emphasised and the other dimmed, so the row reads as "this is what you
// have, and this is what you would have instead". A prefix identical on all 25 rows would carry
// no information at all, which is why the earlier one-line version made the panel worse.

import { el } from "/static/util.js";

/**
 * `doc` is a library doc row ({slug, summary, effect:{with,without,when}}); `on` is the
 * control's current state. A doc missing its `effect:` falls back to the title, marked, so the
 * gap is visible rather than silently reading as a description.
 */
export function effectLine(doc, on) {
  // the doc keys are `with`/`without` (a bare YAML `on:` is the boolean true); the LABELS are
  // the toggle's own words, which is what a reader is looking at
  const e = doc.effect || {};
  if (!e.with && !e.without) {
    return el("div", { class: "effect-line", "data-effect": doc.slug },
      el("span", { class: "effect-text missing" },
         `${doc.summary || "(no description)"} — this doc has no effect: block yet`));
  }
  const side = (key, label, active) => (e[key]
    ? el("div", { class: `effect-side${active ? " active" : ""}`, "data-effect-side": key },
        el("span", { class: "effect-tag" }, label), el("span", { class: "effect-text" }, e[key]))
    : null);
  return el("div", { class: "effect-line", "data-effect": doc.slug },
    ...[side("with", "on", on), side("without", "off", !on),
        // "when" and not "hold it when": all three labels share one column, so the longest
        // one sizes it — a phrase there either overruns into the sentence or steals the width
        // the sentence needs. Italic, and sitting under on/off, is what makes the short word
        // read as "when to use it". The MODIFIER is `advice`, not `when`: `.when` is the
        // console's timestamp class and carries white-space:nowrap, which silently stopped
        // this one row from wrapping and pushed it off the card.
        e.when ? el("div", { class: "effect-side advice" },
          el("span", { class: "effect-tag" }, "when"),
          el("span", { class: "effect-text" }, e.when)) : null].filter(Boolean));
}
