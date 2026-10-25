// A run's own RECIPE REVISIONS, proposed at its finish (D200, engine/recipeproposal.py).
//
// Its own band and its own row for the same reason the swap and unowned cards have theirs: the
// two buttons are not the queued-creation pair. Nothing is CREATED here — the proposing run
// already reasoned the change and could not write it (a scheduled run holds no write_recipe),
// so "apply it" hands that run the capability back: the leg is resumed with its recipe writable
// and makes the edits itself (web/api_recipe_revision.py → engine/revise.py). A generic "create
// it" button on this kind could only ever 400, which is what pending.js's own comment warns
// about for an unmatched kind.
//
// The LINES are the card, not a fold-away detail: they are what the operator is approving, in
// the run's own words, and there are at most engine/recipeproposal.MAX_LINES of them.

import { api } from "/static/api.js";
import { confirmDialog } from "/static/components/dialog.js";
import { el, toast, toastError, when } from "/static/util.js";

export const RECIPE = "recipe-revision";

export function recipeRow(rec, { reload, onChanged } = {}) {
  const f = rec.fields || {};
  const lines = f.lines || [];
  const apply = el("button", { class: "btn small primary" }, "apply it");
  const drop = el("button", { class: "btn small danger" }, "discard");
  const act = async (fn) => {
    apply.disabled = drop.disabled = true;
    try { await fn(); await reload?.(); onChanged?.(); }
    catch (err) { toastError(err, 5000); apply.disabled = drop.disabled = false; }
  };
  apply.onclick = () => act(async () => {
    const r = await api(`/api/pending-creations/${rec.id}/revise`, { method: "POST" });
    toast(`${r.routine} resumed with its recipe writable — it is making the edit`, 5000);
  });
  drop.onclick = () => act(async () => {
    // a cancelled confirm is a choice, not a failure — it must not reach act()'s error toast
    if (!(await confirmDialog(
      `Discard ${rec.routine}'s proposed recipe revision? It is told, so its next run knows the `
      + "change was not wanted.", { confirmLabel: "discard" }))) throw new Error("");
    await api(`/api/pending-creations/${rec.id}/discard`,
      { method: "POST", body: { reason: "the revision was not wanted" } });
    toast("discarded — the proposing routine was told");
  });
  return el("div", { class: "card mt", "data-recipe": rec.id },
    el("div", { class: "row", style: "gap:10px;align-items:center" },
      el("span", {}, el("strong", {}, rec.routine || "?"),
        ` proposes ${lines.length} revision${lines.length === 1 ? "" : "s"} to its own recipe`),
      el("span", { style: "margin-left:auto" }),
      el("a", { class: "btn small", href: `#/routine/${rec.routine}` }, "open"),
      apply, drop),
    el("div", { class: "mt" }, ...lines.map((line) => el("div", { class: "small",
      "data-recipe-line": "" }, el("code", {}, "→"), " ", line))),
    el("div", { class: "faint small" },
      rec.run_id
        ? el("span", {}, "proposed in ", el("a", { href: `#/run/${rec.run_id}` }, rec.run_id))
        : "proposed at a finish",
      " · ", when(rec.created_at),
      " · applying resumes that run with its recipe writable"));
}
