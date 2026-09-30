// Routine settings — IDENTITY & RECIPE: what the routine is called and how it is found — name,
// description, tags, the Steward-hub heading its card sits under. Behind "more": where it came
// from (read-only), its recipe files, the recipe's health, and its state and ledger.
//
// The recipe editor, the health table's roll-back and the cautions' delete are ACTIONS on files
// and tallies the routine owns, not settings: each takes effect at once, on its own button.

import { el, skeleton } from "/static/util.js";
import { settingsSection } from "/static/components/settings-section.js";
import { fieldBlock, settingsGroup } from "/static/components/settings-field.js";
import { tagsEditor } from "/static/components/tags.js";
import { wireRecipeNav } from "/static/resizable.js";
import { mountHealth } from "/static/views/routine-health.js";
import { mountRecipe } from "/static/views/routine-recipe.js";

/** The longest hub heading the PATCH accepts (api_routine_patch.HUB_TAB_MAX). */
const HUB_TAB_MAX = 60;

/** A text field that keeps an empty value out of the draft — a routine needs a name and a
 *  description — and says so under the field instead. */
function textField(node, { required, set, hint }) {
  const problem = el("div", { class: "fl-problem", hidden: true }, hint);
  node.addEventListener("input", () => {
    const v = node.value.trim();
    problem.hidden = !(required && !v);
    if (v || !required) set(v);
  });
  return el("div", {}, node, problem);
}

export function identityGroup(ctx, { titleH1, recipeFile = "" }) {
  const { slug, d, form } = ctx;

  const name = fieldBlock(form, "name", (value, set) => textField(
    el("input", { type: "text", value: value || "", placeholder: "routine name", "data-nopersist": true,
      "data-name": "", style: "width:100%;max-width:420px" }),
    { required: true, set, hint: "a routine needs a name — an empty one is not kept" }));

  const description = fieldBlock(form, "description", (value, set) => textField(
    el("textarea", { rows: "3", "data-nopersist": true,
      placeholder: "what this routine does — a short summary shown on the dashboard and here",
      style: "width:100%;max-width:640px;resize:vertical" }, value || ""),
    { required: true, set, hint: "a routine needs a description — an empty one is not kept" }));

  const tags = fieldBlock(form, "tags", (value, set) =>
    tagsEditor(value || [], async (next) => set(next)));

  const hub = fieldBlock(form, "hub_tab", (value, set) => textField(
    el("input", { type: "text", value: value || "", maxlength: String(HUB_TAB_MAX), "data-nopersist": true,
      placeholder: "e.g. FAU", "data-hub-tab": "", style: "width:100%;max-width:320px" }),
    { required: false, set, hint: "" }));

  // -- origin: the library workflow this routine was generated from (provenance only)
  const wf = d.workflow_ref || {};
  const origin = el("div", {},
    el("span", { class: "ref-tag" }, wf.slug || "hand-authored"),
    el("span", { class: "muted small", style: "margin-left:10px" },
      wf.slug
        ? (wf.in_library
          ? "the library workflow this routine was generated from — its recipe is the routine's OWN now"
          : "its origin workflow is no longer in this library — the recipe is the routine's OWN")
        : "written directly, not generated from a library workflow"));

  // -- recipe: the routine's OWN workflow files — a navigable tree; edits save at once
  const navCol = el("div", { class: "recipe-navcol" }, skeleton(["80%", "60%", "70%"]));
  const editorCol = el("div", { class: "recipe-editorcol" },
    el("div", { class: "muted small" }, "pick a file on the left to view or edit it"));
  // the file tree's resize grip goes in beside it, so the pair needs its wrapper first
  const recipeWrap = el("div", { class: "recipe-wrap" }, navCol, editorCol);
  wireRecipeNav(navCol);
  const recipe = mountRecipe(navCol, editorCol, slug, recipeFile);

  // -- recipe health: runs bucketed by the recipe version that produced them, the regression
  // flag on the newest change, the one-click roll-back — and the cautions the run raised here
  const healthBox = el("div", {}, skeleton(["60%", "90%"]));
  const health = mountHealth(healthBox, slug, { onRecipeChanged: recipe.refreshTree });

  const stateFiles = (d.files?.state) || [];
  const node = settingsGroup({
    form, title: "Identity & recipe", hint: "name · description · tags · hub tab · the recipe",
    keys: ["name", "description", "tags", "hub_tab"],
    digest: () => `origin ${wf.slug || "hand-authored"} · the recipe · its health · state & memory`,
    sections: [
      ...settingsSection({ title: "Name", id: "name" },
        ["the display name (the folder ", el("span", { class: "ref-tag" }, slug), " stays the identity)"],
        name.node),
      ...settingsSection({ title: "Description", id: "description" },
        "a short summary of what this routine does — shown on the dashboard and here",
        description.node),
      ...settingsSection({ title: "Tags", id: "tags" },
        ["freeform labels for filtering on the dashboard (e.g. meta tucks a routine away by ",
         "default)"],
        tags.node),
      ...settingsSection({ title: "Hub tab", id: "hub-tab" },
        "the heading this routine's card sits under on the Steward hub — its runs are told to "
        + "publish the card there; leave empty for none",
        hub.node),
    ],
    more: [
      ...settingsSection({ title: "Origin", id: "origin" },
        "which library workflow this routine was generated from — provenance, read-only.",
        origin),
      ...settingsSection({ title: "Recipe", id: "recipe" },
        ["the routine's OWN workflow — ", el("strong", {}, "main.md"), " routes through the ",
         el("strong", {}, "stage"), " modules (in run-flow order). A saved file takes effect at ",
         "the next run; the routine-improver may refine these too. The general rules it holds ",
         "live in the library, not here."],
        recipeWrap),
      ...settingsSection({ title: "Recipe health", id: "recipe-health" },
        ["runs by the recipe version that produced them, with the regression flag on the newest ",
         "change and its roll-back — and the cautions raised here: the reminders in force with ",
         "this routine's own tally and the rule assists that have fired."],
        healthBox),
      ...settingsSection({ title: "State & memory", id: "state" }, null,
        el("div", { class: "muted small" },
          stateFiles.length ? `state/ · ${stateFiles.join("  ·  ")}` : "no state files yet"),
        el("details", { class: "mt" }, el("summary", { style: "cursor:pointer" }, "LEDGER tail"),
          el("pre", { class: "doc mt" }, d.ledger_tail || "(empty)"))),
    ],
  });
  // the header shows the name: it follows the accepted value, not the draft
  ctx.onDetail(() => { titleH1.textContent = d.name || slug; });
  // a deep link to one recipe file (#/routine/<slug>?file=…) lands with the recipe unfolded
  if (recipeFile) {
    node.open = true;
    for (const fold of node.querySelectorAll("details.rmore")) fold.open = true;
  }
  return { node, health };
}
