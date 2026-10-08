// The production routine page's ONE line about development (operator, 2026-10-08: "clearly
// distinguish between production related information and development related information … not
// to clutter the interface"). Everything about how the routine's CHANGES did — the verdicts, the
// model fit, the recipe-version table and its regression banner, the cautions' tallies — lives in
// its development view (#/changes/<slug>, views/changes-routine.js); this line is the way there:
//
//   DEVELOPMENT · 3 measured changes · latest ▲ IMPROVED
//
// A quiet link, in the iris of the rail's Develop group. When the newest change REGRESSED, or
// recipe health flags the newest recipe change, it wears SUMMONS instead: there a person should
// look. Read from GET /api/changes/<slug>/summary — small enough to re-read when this routine's
// run finishes (views/routine.js does, from its own bus handler); only the newest read paints,
// and none after `dispose`.

import { api } from "/static/api.js";
import { verdictChip } from "/static/components/change-marks.js";
import { el } from "/static/util.js";

const sep = () => el("span", { class: "dev-sep", "aria-hidden": "true" }, "·");

export function devLine(slug) {
  const tag = () => el("span", { class: "dev-tag" }, "development");
  const node = el("a", { class: "dev-line", href: `#/changes/${encodeURIComponent(slug)}`,
                         "data-dev-line": "",
                         title: "this routine's changes, model fit and recipe health" }, tag());
  let alive = true, seq = 0;

  async function reload() {
    const mine = ++seq;
    let s;
    try { s = await api(`/api/changes/${encodeURIComponent(slug)}/summary`); }
    catch { return; /* the bare link still leads to the development view */ }
    if (!alive || mine !== seq) return;
    const regressed = s.latest === "regressed";
    node.classList.toggle("summons", regressed || Boolean(s.recipe_regression));
    const parts = [tag(), sep(), s.changes
      ? el("span", {}, el("span", { class: "mono" }, String(s.changes)),
        ` measured change${s.changes === 1 ? "" : "s"}`)
      : el("span", {}, "no measured change yet")];
    if (s.latest) parts.push(sep(), "latest ", verdictChip(s.latest, { summons: regressed }));
    if (s.recipe_regression) {
      parts.push(sep(), el("span", { class: "dev-flag", "data-recipe-flag": "",
        title: `possible regression since recipe change ${s.recipe_regression.short} — `
          + `"${s.recipe_regression.subject}"` }, "recipe regression flagged"));
    }
    node.replaceChildren(...parts);
  }

  reload();
  return { node, reload, dispose: () => { alive = false; } };
}
