// The WORKING PLAN strip (D54): a run's living decomposition (state/plan.md), shown as an
// always-visible collapsible strip at the top of the run view — the same store the engine
// inlines into the prompt, surfaced to the reader so "where is this run in its own plan" is
// answerable at a glance. Home-agnostic: keyed by run id, it works for a routine run, a
// conversation, or a detached task alike (the backend resolves the owning dir). When the run
// keeps no plan (a scheduled routine's spine is its compiled recipe, or the plan was deleted
// at finish) the strip renders nothing and takes no space.

import { api } from "/static/api.js";
import { md } from "/static/md.js";
import { el } from "/static/util.js";

export function createPlanStrip(mount, { url }) {
  const body = el("div", { class: "plan-body" });
  const box = el("details", { class: "plan-strip", open: true, hidden: true },
    el("summary", { class: "small" }, "working plan"),
    body);
  mount.append(box);

  let last = null;   // the plan text currently rendered — skip a re-render when unchanged
  let alive = true;

  async function refresh() {
    let plan;
    try { ({ plan } = await api(url)); }
    catch { return; }   // transient — keep the last-rendered plan rather than blanking it
    if (!alive || plan === last) return;
    last = plan;
    if (plan) { body.replaceChildren(md(plan)); box.hidden = false; }
    else { body.replaceChildren(); box.hidden = true; }
  }

  refresh();
  return { refresh, destroy() { alive = false; } };
}
