// The CONVERSATION RAIL, built in one place: every section it holds, the component behind each,
// and the teardown they all need.
//
// Extracted from `views/conversations.js`, which was at its ratchet ceiling with zero slack when
// D306 point 2 added the branches section — and extracted along this seam rather than wherever
// there was room. The rail is one responsibility: which sections exist, in what order, which
// component fills each, and what has to be stopped when the view is replaced. The view's own job
// is the reply cycle — the chat, the composer, the live tail, the state transitions — and it kept
// all of that.
//
// WHAT RIDES HERE, in rail order:
//   phases      the conversation's phase graph, lit live from the run state
//   children    the sub-run tree (only when there is a run)
//   files       that run's file activity
//   background  detached tasks this conversation owns (D308 pt 2, components/bg-tasks.js)
//   branches    the branch family with each one's state (D306 pt 2, components/branch-rail.js) —
//               the only section FED FROM OUTSIDE, by the header's own lineage read
//   artifacts   the artifact panel
//   scripts     the conversation's own scripts/ (F833)
//
// NO BROWSER SECTION, deliberately (F561, the operator 2026-10-01: "we wanna remove the browser
// preview in the sidebar. it is redundant now that we have the browser preview chip"): the
// permanent dock shows the shared browser on every page, and the rail copy was a second, POLLED,
// staler view — "● RUNNING" over an empty box after a stop.

import { createArtifacts } from "/static/components/artifacts.js";
import { createBackgroundTasks } from "/static/components/bg-tasks.js";
import { mountBranchSection } from "/static/components/branch-rail.js";
import { createFileActivity } from "/static/components/fileactivity.js";
import { createPhaseGraph } from "/static/components/phasegraph.js";
import { createRail } from "/static/components/rail.js";
import { mountRunScripts } from "/static/components/runscripts.js";
import { createSubRunTree } from "/static/components/subruntree.js";
import { el } from "/static/util.js";

/** Build the whole rail into `host`.
 *
 * `branchBody` comes from the CALLER because the header is rendered before the rail exists and
 * the header's branch bar is what fills it: one lineage read serves the header's line and the
 * rail's rows, instead of the section fetching the same answer a second time.
 *
 * Returns the handles the view actually drives — `artifacts` (the chat opens paths in it),
 * `bgTasks` and `subRunTree`/`fileActivity`/`scripts`/`phaseGraph` (refreshed on a state change)
 * — plus `stop()`, which is every teardown the sections need, in one call the view cannot forget
 * half of.
 */
export function buildConversationRail(host, {
  slug, detail, isLive, family, branchBody,
} = {}) {
  host.replaceChildren();
  // R341: the SHARED rail component (components/rail.js) — the run view renders the same one, so
  // per-section collapse (F296, R262 pt1) is one implementation, not two.
  const rail = createRail(host);
  const graphBody = rail.add("phases", el("div", {}));
  const treeBody = detail.run_id ? rail.add("children", el("div", {})) : el("div", {});
  const filesBody = detail.run_id ? rail.add("files", el("div", {})) : el("div", {});
  const bgBody = rail.add("background", el("div", { class: "bg-tasks" }));
  rail.toggle("background", false);                 // hidden until there is at least one task
  mountBranchSection(rail, branchBody, family);
  const artsBody = rail.add("artifacts", el("div", {}));

  const phaseGraph = createPhaseGraph(graphBody, {
    graphUrl: `/api/conversations/${slug}/phasegraph`,
    ...(detail.run_id ? { statsUrl: `/api/runs/${detail.run_id}/phases` } : {}) });
  const subRunTree = detail.run_id ? createSubRunTree(treeBody, {
    treeUrl: `/api/runs/${detail.run_id}/tree`, isLive }) : null;
  const fileActivity = detail.run_id
    ? createFileActivity(filesBody, { url: `/api/runs/${detail.run_id}/files` }) : null;
  const artifacts = createArtifacts(artsBody, { slug });
  const scripts = mountRunScripts(rail, { slug, base: "conversations" });
  // D308 pt 2: each row carries its elapsed time while live, its DELIVERY once terminal, a link
  // to the task's own run page and the files it landed. It refreshes on the `run_started` /
  // `run_finished` events of the runs it owns, so no poll asks every tab about an empty list.
  const bgTasks = createBackgroundTasks(bgBody, { slug, rail,
    openArtifact: (p) => artifacts.openPath(p) });
  bgTasks.paint(detail.background || []);

  return {
    rail, phaseGraph, subRunTree, fileActivity, artifacts, scripts, bgTasks,
    //: Everything the sections hold open — one call, so a view replacing itself cannot stop
    //: three of four and leak the fourth.
    stop: () => {
      bgTasks.stop();
      artifacts.destroy();
      subRunTree?.stop();
      scripts.stop();
    },
  };
}
