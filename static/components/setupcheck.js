// Setup check — what this routine still needs, above everything else on its page.
//
// The panels below it show what you SET. This shows what that adds up to: a rule bound to a
// routine with no root to publish into, a reserved util whose credential store no grant covers,
// a secret declined forever on a channel the routine is still told to use. None of it was
// visible anywhere before, because the system declared only one of its own dependencies and the
// rest surfaced as a run failing mid-way.
//
// Rows are ordered by what they COST, worst first, and each says it in those terms rather than
// in severity abstractions: "the call is rejected or fails" / "the run stops to ask you" /
// "worth knowing". A routine with nothing outstanding renders nothing at all — a panel that is
// always there is a panel nobody reads.

import { api } from "/static/api.js";
import { el } from "/static/util.js";

const LABEL = { blocks: "fails", interrupts: "interrupts", note: "note" };

/** One row: what it is, why it is needed, what it costs. */
function row(node) {
  return el("div", { class: `setup-row sev-${node.severity}`, "data-entity": node.id },
    el("span", { class: "setup-sev" }, LABEL[node.severity] || node.severity),
    el("div", {},
      el("div", { class: "setup-id" }, node.id),
      el("div", { class: "muted small prose" },
        node.why, node.effect ? ` — ${node.effect}` : "")));
}

/**
 * The strip. Renders into `host` and resolves when the surface has been read; a failure is
 * silent by design — a diagnostic that shouts about its own unavailability is worse than one
 * that is quietly absent.
 */
export async function setupCheck(host, slug) {
  let surface;
  try {
    surface = await api(`/api/routines/${slug}/surface`);
  } catch {
    return null;
  }
  const unmet = (surface.nodes || []).filter((n) => n.severity !== "ok");
  if (!unmet.length) return surface;
  const v = surface.verdict || {};
  const counts = [
    v.blocks ? `${v.blocks} will fail` : "",
    v.interrupts ? `${v.interrupts} will interrupt` : "",
    v.notes ? `${v.notes} note${v.notes > 1 ? "s" : ""}` : "",
  ].filter(Boolean).join(" · ");
  host.append(el("div", { class: `setup-check${v.blocks ? " has-blocks" : ""}`,
                          "data-setup-check": "" },
    el("div", { class: "setup-head" },
      el("span", {}, "⚑"), el("b", {}, "Setup check"), el("span", { class: "muted" }, counts)),
    ...unmet.map(row)));
  return surface;
}
