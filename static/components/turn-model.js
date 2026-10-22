// What SERVED a turn, in the turn's own row (D188; operator 2026-10-16: "we need to see with
// each turn which model was used").
//
// The engine stamps the resolved model and the role that resolved to it on the turn's usage
// (engine/completion.py, `usage["model"]` / `usage["role"]`), and the one honest reading of
// "which model was used" is that stamp: deriving it from config at render time would answer
// "which model WOULD be used now", which is exactly wrong for the run this exists to audit —
// one that failed over, or whose refused turn the uncensored model took.
//
// Lives in its own module because static/components/transcript.js sits at its size-ratchet
// ceiling: the call site there is one line.

import { el, fmtTokens } from "/static/util.js";

// "main → claude-proxy/claude-opus-5" when the role is known, the bare model when it is not,
// and "" for a turn recorded BEFORE the field existed — every archived run predates it, and a
// row reading "null" or "undefined" on a year of them is worse than the blank already there.
function modelText(usage) {
  const model = usage && usage.model;
  if (!model) return "";
  return usage.role ? `${usage.role} \u2192 ${model}` : String(model);
}

// The turn row's right-hand meta: what served it, then what it cost. Null when the turn has no
// usage at all (an event the engine wrote outside a completion).
export function turnUsage(ev) {
  const usage = ev && ev.usage;
  if (!usage) return null;
  const served = modelText(usage);
  return el("span", { class: "muted turn-usage", style: "margin-left:auto",
                      title: usage.provider ? `served by ${usage.provider}` : "" },
    served ? el("span", { class: "turn-model" }, served) : null,
    served ? " \u00b7 " : null,
    fmtTokens(usage));
}
