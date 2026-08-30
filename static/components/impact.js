// The BLAST RADIUS of a library edit, on the Library tab.
//
// There is exactly one copy of every rule, permission, template and util, so a save here
// reaches every routine that holds it at that routine's next run, with no migration and
// nothing to review. `library_impact.py` computes what that costs — each holder's setup
// surface against the current library and against the proposed one — and the API has served
// it since 0.256.0, but nothing on the page ever asked. The tab saved blind; the server's
// only defence was a 409 naming a confirm token no UI could echo back: a dead end.
//
// So the preview is not an extra button here — it is part of saving and part of deleting.
// A change that breaks nobody goes straight through (the token is not even required then);
// a change that breaks somebody names WHO and WHAT they gain — and asks.

import { api } from "/static/api.js";
import { confirmDialog } from "/static/components/dialog.js";
import { el } from "/static/util.js";

// `gained` rows are "<severity>:<entity>" — the surface's own vocabulary (readmodels/surface.py),
// worst first. The words are about CONSEQUENCE, which is what an operator is deciding on.
const SEVERITY = {
  blocks: "the call is rejected or fails — the run cannot do the thing",
  interrupts: "the run stops mid-way to ask, spending a turn and your attention",
};

function breakRows(breaks) {
  return breaks.map((b) => el("div", { class: "impact-break", "data-breaks": b.slug },
    el("b", {}, b.slug),
    el("ul", { class: "impact-gains" }, b.gains.map((g) => {
      const [severity, ...rest] = g.split(":");
      return el("li", {}, el("span", { class: `chip ${severity === "blocks" ? "failed" : "warn"}` },
                             severity),
        " ", el("code", {}, rest.join(":")),
        SEVERITY[severity] ? el("div", { class: "faint small" }, SEVERITY[severity]) : null);
    }))));
}

/** A live "who holds this" line under the editor, plus the gate both save and delete run
 *  through. `kind` is the API path segment (utils | rules | permissions | templates). */
export function impactPanel(kind, slug) {
  const node = el("div", { class: "impact-panel", "data-impact": `${kind}/${slug}` },
    el("span", { class: "faint small" }, "checking who holds this…"));

  const fetchImpact = (content) =>
    api(`/api/library/${kind}/${slug}/impact`, { method: "POST", body: { content } });

  function paint(result, { proposed }) {
    const holders = result.holders || [];
    if (!holders.length) {
      node.replaceChildren(el("span", { class: "faint small" },
        "no routine holds this yet — a change here reaches nobody."));
      return;
    }
    const head = el("div", { class: "small" },
      el("b", {}, `${holders.length} routine${holders.length > 1 ? "s" : ""} hold${holders.length > 1 ? "" : "s"} this`),
      " · ", holders.join(", "),
      el("div", { class: "faint small" },
        "each picks the change up at its next run — there is one copy and no migration."));
    node.replaceChildren(head);
    if (!proposed) return;      // the on-open read states WHO; only a proposal states WHAT
    if (result.breaks?.length) {
      node.append(el("div", { class: "impact-breaks mt" },
        el("div", { class: "small" }, "⚠ this version would break:"), ...breakRows(result.breaks)));
    } else {
      node.append(el("div", { class: "faint small mt" }, "✓ breaks none of them"));
    }
  }

  // On open: WHO holds it, computed against the doc as it stands (content: null asks the
  // deletion question, whose holder list is the same one).
  (async () => {
    try { paint(await fetchImpact(null), { proposed: false }); }
    catch { node.replaceChildren(); }     // a diagnostic must never break the editor
  })();

  /** Run the gate for `content` (null = deletion). Returns the digest to send with the write,
   *  or null when the user backed out. A change that breaks nobody returns "" — the server
   *  asks for no token then; neither do we. */
  async function gate(content, { verb }) {
    let result;
    try { result = await fetchImpact(content); }
    catch { return ""; }        // the preview is informational: never block a save on it
    paint(result, { proposed: true });
    if (!result.breaks?.length) return "";
    const detail = el("div", {},
      el("div", {}, `This ${verb} breaks ${result.breaks.length} routine`
        + `${result.breaks.length > 1 ? "s" : ""} that hold${result.breaks.length > 1 ? "" : "s"} `
        + "it. They pick it up at their next run — nobody reviews it there."),
      el("div", { class: "impact-breaks mt" }, ...breakRows(result.breaks)));
    const ok = await confirmDialog(detail,
      { confirmLabel: `${verb} anyway`, danger: true });
    return ok ? result.digest : null;
  }

  return { node, gate };
}
