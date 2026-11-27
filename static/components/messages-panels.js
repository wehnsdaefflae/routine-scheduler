// The MESSAGES page's two stream readings, above its item list and outside every filter below
// it. Both are readings of what the fleet did rather than of any one item, which is why they
// live here together and why this file exists at all: `views/messages.js` is over the house
// size bar, so the wiring a panel needs (a storage key, a lazy start, a teardown) belongs
// beside the panels instead of in the view.
// WHY THESE TWO, HERE: most of the hook stream's rows ARE the report ledger this page renders,
// and a run's outcome is what its summaries are — so "what did everything I run just tell me"
// and "what is running" are one question, read on one page. The Routines page keeps what is
// about the ROUTINES: the week strip, the lanes and the bays.
//
// The activity feed stays LAZY exactly as it was on the Dashboard — `isOpen` is what keeps it
// lazy after its first open, because start() arms a 4 s poll over three endpoints that has to
// stop again when the section closes (test_view_teardown.py). The hook panel starts eagerly:
// it is one fetch and one 60 s refresh of a single fold.

import { activityFeed } from "/static/components/activityfeed.js";
import { hookActivityPanel } from "/static/components/hook-activity.js";
import { el, storage } from "/static/util.js";

const ACTIVITY_KEY = "rsched_messages_activity";

/**
 * The page's stream readings: `{ nodes, dispose }`. `nodes` is in render order — hook panel
 * first (what fires next and who spoke), then the activity feed (what ran). `dispose()` stops
 * every poll and listener both of them armed; the view's own teardown must call it.
 */
export function messagesPanels() {
  const hooks = hookActivityPanel();
  hooks.start();

  // Collapsed unless this browser last left it open — the same remembered-state contract the
  // Dashboard's section had, under a key of its own so the move does not inherit a stale one.
  const feed = activityFeed({ isOpen: () => activityPanel.open });
  const activityPanel = el("details", { class: "panel mt activity-panel",
    ...(storage.get(ACTIVITY_KEY) === "open" ? { open: true } : {}) },
    el("summary", {}, "activity — every run across every routine"), feed.node);
  activityPanel.addEventListener("toggle", () => {
    storage.set(ACTIVITY_KEY, activityPanel.open ? "open" : "closed");
    if (activityPanel.open) feed.start();
  });
  if (activityPanel.open) feed.start();

  return {
    nodes: [hooks.node, activityPanel],
    dispose() { feed.dispose(); hooks.dispose(); },
  };
}
