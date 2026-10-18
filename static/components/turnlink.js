// Addressing a TURN in a transcript — the one mechanism every rail card uses to send a
// reader to where something happened (D193: the phase graph's nodes, the sub-run tree's rows).
//
// A turn box carries `data-turn="<n>"` (components/transcript.js stamps it when it builds the
// box), so the address of a turn is that attribute and nothing else: no anchor scheme, no ids
// to keep unique across the main transcript and the sub transcripts mounted beside it.
//
// `wireTurnLinks(root, runId)` does BOTH halves in one call and returns the href builder the
// cards want:
//   • it installs ONE delegated click listener on `root` — the view's own container, which the
//     router discards whole on navigation (static/app.js renders each view into a fresh box),
//     so there is nothing to tear down and nothing that can outlive the view;
//   • it lands on `?turn=` at mount, so a deep link into a run opens on that turn;
//   • it returns `(turn) => href`, which a card puts on its node.
//
// Landing is landing.js's two moves — open every fold on the way, then flash what you landed
// on — the same journey a ref link (components/reflinks.js) or a setup fix makes.

import { flash, openFolds } from "/static/landing.js";
import { setQuery } from "/static/router.js";

// How long a mount keeps looking for the turn a `?turn=` deep link names. The transcript's
// first page arrives over the network after the view mounts, so the box does not exist yet
// when this runs; past this the link is stale (a turn retention pruned, a rewound run) and
// quietly giving up beats scrolling somewhere arbitrary.
const LAND_WINDOW_MS = 6000;
const LAND_POLL_MS = 150;

// The console address of one turn of one run. Module-local: callers get it from
// wireTurnLinks' return value, which already knows the run.
const turnHref = (runId, turn) => `#/run/${encodeURIComponent(runId)}?turn=${turn}`;

/** The turn box for `turn` inside `root`, or null.
 *
 *  Scoped to the OUTERMOST transcript on the page, because turn numbers are only unique
 *  within one run: an expanded sub-run fold mounts the CHILD's transcript inside the parent's
 *  DOM, and that child has a turn 3 of its own. Taking the first match in document order
 *  would hand a reader the wrong run's turn whenever a fold above it is open. */
function turnBox(root, turn) {
  const main = root.querySelector(".transcript");
  // A HIDDEN main transcript is not a destination: the run view keeps it mounted underneath
  // while a sub-run tab is open (views/run.js `mainBox.hidden`), so scrolling to a box inside
  // it would land the reader on nothing. Falling through to the href re-renders the view on
  // the main transcript, which is where the turn actually is.
  if (!main || main.closest("[hidden]")) return null;
  for (const box of main.querySelectorAll(`.turn[data-turn="${CSS.escape(String(turn))}"]`)) {
    if (box.closest(".transcript") === main) return box;
  }
  return null;
}

/** Scroll to the turn and flash it. Returns false when it is not on the page. */
function landOnTurn(root, turn) {
  const box = turnBox(root, turn);
  if (!box) return false;
  openFolds(box);
  box.scrollIntoView({ block: "center" });
  flash(box);
  return true;
}

export function wireTurnLinks(root, runId) {
  root.addEventListener("click", (e) => {
    const a = e.target.closest?.("[data-turn-link]");
    if (!a || !root.contains(a)) return;
    const turn = a.getAttribute("data-turn-link");
    // In-page landing is the whole point: the transcript is already mounted and live, so
    // tearing the view down to re-render it would lose the tail's position. Only a turn that
    // is NOT on the page falls through to the href, which reloads the view on it.
    if (!landOnTurn(root, turn)) return;
    e.preventDefault();
    setQuery({ turn });   // the URL says where the reader is, without a remount
  });

  // A `?turn=` deep link (or the fall-through above) lands once the transcript has painted.
  const wanted = new URLSearchParams((location.hash.split("?")[1] || "")).get("turn");
  if (wanted) {
    const started = Date.now();
    const tryLand = () => {
      if (!root.isConnected || Date.now() - started > LAND_WINDOW_MS) return;
      if (!landOnTurn(root, wanted)) setTimeout(tryLand, LAND_POLL_MS);
    };
    setTimeout(tryLand, LAND_POLL_MS);
  }

  return (turn) => turnHref(runId, turn);
}
