// The run page's ROUTINE RAIL: every routine beside the run you are reading, each expandable to
// its previous runs.
//
// OPERATOR ORDER (F619, 2026-10-04): *"i want a list of all routines next to a routine run page
// just like there's a list of all conversations next to a conversation. the only difference is
// that the routines can be expanded to the previous runs"*.
//
// Its OWN module, not growth in views/run.js (583 lines) or views/conversations.js (569): both
// are frozen at their ratchet ceilings (CLAUDE.md Standards, tests/size_ratchet.json), and
// "which routines there are and what they ran" is one responsibility. components/runrail.js
// mounts it, so the run view itself is untouched — the shape 0.382.0 used for ladder-settings.js.
//
// WHAT IT MIRRORS. views/conversations.js's LEFT rail: a `details.run-rail left` host wired by
// the shared wireRunRail (static/resizable.js), a dense one-line-per-item list, the item being
// read marked. Two differences, both straight out of the order:
//
//   * a routine EXPANDS to its previous runs — the one part a conversation has no counterpart
//     for. The routine being read arrives OPEN, because its own runs are why the rail is there;
//     every other row opens on click and reads ITS runs only then, so arriving on a run page
//     costs ONE request however many routines the instance has.
//   * no composer and no tag filter: this is navigation beside a run, not the Routines page,
//     which is where a routine is managed.
//
// A run whose slug is NOT a routine (a conversation's run, a detached task's) gets no rail at
// all — the host is dropped and `.run-view:not(:has(> .run-rail.left))` (static/rail.css) hands
// the column back, rather than leaving an empty 218px of routines beside a chat.
//
// No poller: a routine list does not change while a reader looks at one run, and a rail that
// polled would be one more thing to stop when the view goes (the F324-era leak).

import { api } from "/static/api.js";
import { el, fmtAbs } from "/static/util.js";
import { wireRunRail } from "/static/resizable.js";

//: how far back a row expands. The rail answers "what did this routine do before this run",
//: which is a reading list, not an archive — the routine's own page carries the full index.
const RUNS_N = 12;

/** A state dot in the console's one shape for one, as the conversation list mounts it. */
function dot(state) {
  return el("span", { class: `dot ${state}`, role: "img", title: state,
                      "aria-label": `state: ${state}` });
}

/**
 * Build the routine rail into `view` and fill it.
 *
 * `view` is the run view's container: the host is PREPENDED, because the grid's first column is
 * whatever comes first in the DOM and this is the LEFT rail (views.css) — the same DOM order
 * views/conversations.js mounts (list, main, artifacts).
 */
export function createRoutineRail(view, { runId }) {
  const slug = String(runId).split(":")[0];
  const list = el("div", { class: "rr-list" });
  const host = el("details", { class: "run-rail left", open: true },
    el("summary", { class: "small" }, "routines"),
    el("div", { class: "pane-body" }, list));
  view.prepend(host);
  // Wired at MOUNT, not after the read: wireRunRail decides the phone tier's closed state as it
  // wires (static/resizable.js), and a rail that wired itself one fetch later would open over
  // the run on a phone for exactly as long as that fetch took.
  wireRunRail(host, "left");

  /** Drop the rail AND the grip wireRunRail inserted beside it — the grip is its sibling, so a
   *  removed host would otherwise leave a handle for a rail that is not there. */
  function drop() {
    const grip = view.querySelector(":scope > .sb-grip.runrail-l");
    if (grip) grip.remove();
    host.remove();
  }

  /** One previous run: where it lands, when it ran, and what became of it. */
  function runRow(r) {
    const row = el("a", { class: "rr-run", href: `#/run/${r.run_id}` },
      dot(r.state),
      el("span", { class: "rr-when" }, fmtAbs(r.ts)),
      el("span", { class: "rr-state" }, r.state));
    if (r.run_id === runId) row.classList.add("on");
    return row;
  }

  /** One routine: its row, and the runs the row expands to. */
  function node(info) {
    const runs = el("div", { class: "rr-runs" });
    const name = el("span", { class: "rr-name" }, info.name || info.slug);
    // The row is the EXPANDER, so the way to the routine's own page is its own link: a name
    // that navigated would mean the one click this rail exists for never expands anything.
    const open = el("a", { class: "rr-open", href: `#/routine/${info.slug}`,
                           title: `open ${info.slug}`,
                           onclick: (e) => e.stopPropagation() }, "↗");
    const state = info.active_state || (info.last_run && info.last_run.state) || "idle";
    const row = el("summary", { class: "rr-row" }, dot(state), name, open);
    const box = el("details", { class: "rr-node", "data-routine": info.slug }, row, runs);
    if (info.slug === slug) {
      row.classList.add("on");
      box.open = true;                  // the routine being read arrives at its own runs
    }

    let loaded = false;
    async function fill() {
      if (loaded) return;
      loaded = true;
      let rows;
      try {
        rows = await api(`/api/runs?routine=${encodeURIComponent(info.slug)}&limit=${RUNS_N}`);
      } catch (err) {
        loaded = false;                 // a failed read is retried by folding and reopening
        runs.replaceChildren(el("div", { class: "rr-none" }, `couldn't read runs: ${err.message}`));
        return;
      }
      runs.replaceChildren(...(rows.length
        ? rows.map(runRow)
        : [el("div", { class: "rr-none" }, "no runs yet")]));
    }
    box.addEventListener("toggle", () => { if (box.open) fill(); });
    if (box.open) fill();
    return box;
  }

  const ready = (async () => {
    let routines;
    try {
      routines = await api("/api/routines");
    } catch {
      drop();                           // the rail is navigation: a failed read leaves the run alone
      return;
    }
    if (!routines.some((r) => r.slug === slug)) {
      drop();                           // not a routine's run — see the module note
      return;
    }
    list.replaceChildren(...routines.map(node));
  })();

  return { host, ready };
}
