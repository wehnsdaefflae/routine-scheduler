// The RUN PAGE'S RAIL: which sections it has, in which order, and what fills them. The second
// grid column beside the main column from 760px up (resizable, hideable — its grip is the
// sibling wireRunRail inserts), an ordinary collapsible block below the main column on a phone.
//
// R341 (user steer on R339 + R340: "don't we reuse the same code?"): the rail itself is the
// SHARED component the conversation view renders, so every section is individually collapsible
// and remembered per browser. This module is the run view's own composition of it — split out of
// views/run.js when F646 added the jobs section, because that file is at its size ceiling and
// "which sections the rail has" is one responsibility a reader should be able to see whole.
//
// WHAT THE RUN PRODUCED leads (artifacts, files), then where the run IS (phases, sub-runs), then
// the goal — this run's own accounting of what it answered for — last. Each section's collapsed
// state is remembered per browser, so a reader who wants the goal first keeps it open and
// everything else shut. Two further sections are added LAZILY by their own owners, only for a
// run that has one: `oversight` (components/ladderstrip.js) and `jobs` (components/runjob.js).
//
// The run page has a SECOND rail since F619 (operator, 2026-10-04): the routine list on the
// LEFT, every routine expandable to its previous runs — the counterpart of the conversation list
// beside a chat. It is `components/routine-rail.js`, mounted from here with everything else the
// run page's rails are, and it drops itself for a run whose slug is no routine's.

import { createFileActivity } from "/static/components/fileactivity.js";
import { createRail } from "/static/components/rail.js";
import { createRoutineRail } from "/static/components/routine-rail.js";
import { createSubRunTree } from "/static/components/subruntree.js";
import { wireTurnLinks } from "/static/components/turnlink.js";
import { el } from "/static/util.js";
import { wireRunRail } from "/static/resizable.js";

/**
 * Build the run page's rail into `view` and arm the two pollers that key off the RUN (the
 * sub-run tree and the file activity — both home-free, so they mount before the run detail is
 * read; the two HOME-scoped cards, the phase graph and the artifacts list, are created by the
 * caller into the bodies returned here once the detail names the home).
 *
 * Returns the rail itself (for `add` / `toggle`), the bodies the caller fills, and the two
 * pollers it must refresh and stop.
 */
export function createRunRail(view, { runId, isLive }) {
  // F619, the operator's order of 2026-10-04: the routine list beside the run, the counterpart
  // of the conversation list beside a chat. It is the LEFT rail and prepends its own host, so it
  // is mounted here — "which rails the run page has" is this module's responsibility, and
  // views/run.js (583 lines) is frozen at its ratchet ceiling.
  const routineRail = createRoutineRail(view, { runId });
  const railHost = el("details", { class: "run-rail", open: true },
    el("summary", { class: "small" }, "phases & artifacts"));
  view.append(railHost);
  wireRunRail(railHost, "right");
  const rail = createRail(railHost);
  // D193: the rail's cards are NAVIGATION, not just a readout — a phase node and a sub-run row
  // each land on the turn they name. One delegated listener on the whole view serves both, and
  // the view's container is discarded on navigation, so nothing here outlives the page.
  const turnHref = wireTurnLinks(view, runId);
  const artBody = rail.add("artifacts", el("div", {}));
  const filesBody = rail.add("files", el("div", {}));
  const graphBody = rail.add("phases", el("div", {}));
  const treeBody = rail.add("sub-runs", el("div", {}));
  const goalBody = rail.add("goal", el("div", {}));
  const subRunTree = createSubRunTree(treeBody, {
    treeUrl: `/api/runs/${runId}/tree`, isLive, turnHref });
  const fileActivity = createFileActivity(filesBody, { url: `/api/runs/${runId}/files` });
  return { rail, artBody, filesBody, graphBody, goalBody, subRunTree, fileActivity, turnHref,
           routineRail };
}
