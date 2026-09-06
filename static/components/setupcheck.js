// Setup check — what this routine's setup leaves unsettled, above everything else on its page.
//
// The panels below it show what you SET. This shows what that adds up to: a rule bound to a
// routine with no root to publish into, a reserved util whose credential store no grant covers,
// a secret declined forever on a channel the routine is still told to use. None of it was
// visible anywhere before, because the system declared only one of its own dependencies and the
// rest surfaced as a run failing mid-way.
//
// Rows are ordered by what they COST, worst first; each says it in those terms rather than
// in severity abstractions: "the call is rejected or fails" / "the run stops to ask you" /
// "worth knowing". A routine with nothing outstanding renders nothing at all — a panel that is
// always there is a panel nobody reads.
//
// The filter is UNSATISFIED, which is a wider test than unmet: a row reporting a deliberate
// setting — `action:write_recipe` "on", `schedule:goal` "retired" — is not `ok` either, yet
// has nothing about it to close. Which is which is the server's to say: it names a `fix`, or
// it names none. A row with no act therefore says so in a line of its own. The panel
// below reads an absent offer from its neighbours, settled rows sitting beside offered ones; up
// here every row is a complaint, where silence reads as an offer that failed to render.
//
// Everything that IS unmet carries the act that closes it. The strip is where a failure is read
// first; a diagnosis this far above the fold is the last place to leave somebody with nowhere to
// go. The offer is rendered by surface-view.js, which owns the one map from a fix kind to the
// panel that performs it: this strip and the effective surface show the same row twice, so a
// second copy of that map would be a second set of words for one act. The import therefore runs
// BOTH ways between these two files — that one reads LABEL from here, this one reads the offer
// from there — which resolves because neither touches the other's binding while it evaluates.

import { api } from "/static/api.js";
import { fixLine } from "/static/components/surface-view.js";
import { el } from "/static/util.js";

// Severity → what it COSTS, in the reader's terms rather than the surface's abstractions. One
// copy, because surface-view.js renders the very same join unfiltered and the two strips would
// otherwise be free to disagree about what "blocks" means to a person. `ok` only ever shows up
// there — this strip renders nothing that is already satisfied.
export const LABEL = { blocks: "fails", interrupts: "interrupts", note: "note", ok: "ok" };

// What a row with no fix says in place of an offer. The strip has no column of settled rows to
// read the silence against, so the silence is spelled out.
const NO_ACT = "nothing to do — this states how the routine is set, not something missing";

/** One row: what it is, why it is needed, what it costs, and what to do about it. The act sits
 *  UNDER the sentence it answers rather than inside it — the diagnosis is prose written about
 *  this routine, while the remedy is the console instructing the reader, so the two faces are
 *  set apart already. A row the server named no fix for carries the standing line instead; one
 *  whose kind the FIX map has no words for carries neither, because guessing at an act is the
 *  failure the offer exists to prevent — a state the binding test forbids from ever shipping,
 *  so it reds in `tests/ui/test_surface_fix.py` rather than rendering a blank row at anybody. */
function row(node) {
  return el("div", { class: `setup-row sev-${node.severity}`, "data-entity": node.id },
    el("span", { class: "setup-sev" }, LABEL[node.severity] || node.severity),
    el("div", {},
      el("div", { class: "setup-id" }, node.id),
      el("div", { class: "muted small prose" },
        node.why, node.effect ? ` — ${node.effect}` : ""),
      fixLine(node)
        || (node.fix?.kind ? null : el("div", { class: "faint small" }, NO_ACT))));
}

/**
 * The strip. Renders into `host` and resolves to the surface it painted; a failure is silent by
 * design — a diagnostic that shouts about its own unavailability is worse than one that is
 * quietly absent.
 *
 * It OWNS `host`, repainting it from scratch, because the same call is how the page redraws the
 * strip after a change: a complaint left standing over the save that answered it is the one
 * thing a diagnosis this far above the fold must never do. `surface` is a payload already read
 * elsewhere — the config side re-reads once and hands the same answer to every reader of it —
 * and with none the strip reads the endpoint itself.
 */
export async function setupCheck(host, slug, surface = null) {
  let data = surface;
  if (!data) {
    try {
      data = await api(`/api/routines/${slug}/surface`);
    } catch {
      host.replaceChildren();
      return null;
    }
  }
  host.replaceChildren();
  const unsatisfied = (data.nodes || []).filter((n) => n.severity !== "ok");
  if (!unsatisfied.length) return data;
  const v = data.verdict || {};
  const counts = [
    v.blocks ? `${v.blocks} will fail` : "",
    v.interrupts ? `${v.interrupts} will interrupt` : "",
    v.notes ? `${v.notes} note${v.notes > 1 ? "s" : ""}` : "",
  ].filter(Boolean).join(" · ");
  host.append(el("div", { class: `setup-check${v.blocks ? " has-blocks" : ""}`,
                          "data-setup-check": "" },
    el("div", { class: "setup-head" },
      el("span", {}, "⚑"), el("b", {}, "Setup check"), el("span", { class: "muted" }, counts)),
    ...unsatisfied.map(row)));
  return data;
}
