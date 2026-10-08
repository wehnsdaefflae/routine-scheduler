// A MODEL TRIAL in the routine page's header (rsched/trials.py): for its next N runs the
// routine runs on another catalog model than its own — production-relevant, so it sits beside
// the run chip as ONE chip. A finished trial points to where its results are read (the
// Development view, #/changes/<slug>). Nothing at all when there is no trial — nor when one is
// IGNORED (a model the catalog lacks): that one is a problem, and the page's problem panel says it.

import { el } from "/static/util.js";

// "sonnet-high" for the main role, "tool_call fast-model" for any other
function modelsLine(models) {
  return Object.entries(models || {})
    .map(([role, name]) => (role === "main" ? name : `${role} ${name}`)).join(", ");
}

export function trialChip(trial, slug) {
  if (!trial) return null;
  const title = [trial.reason, `trial ${trial.id}`].filter(Boolean).join(" · ");
  if (trial.state === "active") {
    return el("span", { class: "chip trial", title, "data-trial": "active" },
      `trial · ${modelsLine(trial.models)} · ${trial.recorded} of ${trial.runs} runs`);
  }
  if (trial.state === "finished") {
    return el("a", { class: "chip trial", title, "data-trial": "finished",
                     href: `#/changes/${encodeURIComponent(slug)}` },
      "trial finished · results in Development");
  }
  return null;
}
