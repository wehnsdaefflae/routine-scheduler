// HOOK ACTIVITY — the hook stream's two readings, on the Routines page beside the watch ribbon:
// "Fires next" (the one-shots still armed, soonest first) and "Spoken into runs" (the last day's
// mid-run injections and run-tree hooks).
//
// Both come from ONE fetch of `/api/hook-activity`, because they are one fold of one file
// (`readmodels/hook_activity.py`): two endpoints would parse `.control/hooks.jsonl` twice per
// refresh to answer halves of the same question.
//
// WHY THIS PANEL EXISTS: every one of those rows has been written for months and read by nobody
// — a one-shot armed from a routine page, a report or an escalation rung speaking into a live
// run, a run-tree hand-back whose write failed. The two questions the stream exists to answer
// for the instance had no answer anywhere in the console.
//
// It refreshes on its OWN 60 s timer and on `run_finished` only — never on every bus tick. A
// finished run is when an injection's row stops changing and when a one-shot gets consumed; the
// other events move nothing here, and a config-shaped read refetched per tick is a storm this
// page has already paid for once.
//
// ZERO IS A READING: an instance with nothing armed and nobody speaking renders both sections
// saying so, because an absent panel and a quiet one are indistinguishable.

import { api } from "/static/api.js";
import { el } from "/static/util.js";

const REFRESH_MS = 60_000;
const HORIZON_H = 24;
const SINCE_H = 24;

// A stamp as a reader wants it in a row: the day and the minute, nothing else.
const when = (ts) => (ts || "").replace("T", " ").slice(0, 16) || "—";

// ZERO IS A READING, and it is rendered as a line of this PANEL's — deliberately not util.js's
// `emptyState`. That builds a page-level `.empty` block, and the Routines page already has one
// for "nothing matches this filter": two more on the same page turned an existing browser test's
// `.empty` locator into a three-element strict-mode violation (test_dashboard_table.py). A
// panel's own quiet reading belongs to the panel.
const quiet = (text) => el("div", { class: "hookact-quiet" }, `◌ ${text}`);

function fireRow(r) {
  return el("div", { class: "hookact-row hookact-fire" },
    el("span", { class: "hookact-when" }, when(r.fires_at)),
    r.routine
      ? el("a", { href: `#/routine/${r.routine}` }, r.routine)
      : el("span", { class: "faint" }, "an unnamed routine"),
    // a pending one-shot whose instant has passed is NOT dropped by the fold: it is one the
    // daemon has not consumed yet, and saying so is the whole point of showing it
    r.overdue ? el("span", { class: "chip bare hookact-overdue",
      title: "its instant has passed and the daemon has not consumed it yet" }, "overdue") : null,
    el("span", { class: "small" }, r.reason || ""),
    r.requested_by
      ? el("span", { class: "faint small" }, ` armed by ${r.requested_by}`)
      : null);
}

function deliveryRow(r) {
  // WHO spoke, in the server's own words: the channel knows, and a raw `web-converse` says
  // nothing to a reader. `person` is `channels.user_authored` — never a second list here. The
  // second class is added rather than interpolated into the first, so both tokens are plain
  // literals the class contract can see (tests/class_survey.py).
  const who = el("span", { class: "hookact-who" }, r.who || r.via);
  if (r.person) who.classList.add("hookact-person");
  return el("div", { class: "hookact-row hookact-delivery" },
    el("span", { class: "hookact-when" }, when(r.ts)),
    r.run_id
      ? el("a", { href: `#/run/${r.run_id}`, title: r.run_id }, r.routine || r.run_id)
      : el("span", { class: "faint" }, "a run that is gone"),
    r.turn ? el("span", { class: "faint small" }, `turn ${r.turn}`) : null,
    who,
    el("span", { class: "small" }, r.text || ""),
    // `open` on a run-tree row means the write FAILED: the stream is the only record of that
    // anywhere, because a run-tree hook deliberately gets no R id
    r.undelivered
      ? el("span", { class: "chip bare hookact-undelivered" }, r.undelivered_note)
      : null);
}

/**
 * The hook-activity panel: `{ node, start, dispose }`. `start()` is idempotent — the first call
 * loads and arms the 60 s timer and the `run_finished` listener; later calls just refresh.
 */
export function hookActivityPanel() {
  const fires = el("div", { class: "hookact-fires" });
  const spoken = el("div", { class: "hookact-spoken" });
  const node = el("details", { class: "panel mt hookact-panel", open: true },
    el("summary", {}, "hook activity — what fires next, and who spoke into which run"),
    el("div", { class: "sub" },
      "one read of the hook stream: the one-shots still armed, and the last day's mid-run "
      + "injections and run-tree hand-backs"),
    el("h3", { class: "hookact-head" }, "Fires next"), fires,
    el("h3", { class: "hookact-head" }, "Spoken into runs"), spoken);

  let started = false, timer = null, inflight = false, disposed = false;

  function paint(d) {
    const armed = d.next_fires || [];
    const said = d.deliveries || [];
    fires.replaceChildren(armed.length
      ? el("div", {}, ...armed.map(fireRow))
      : quiet(`nothing armed — no one-shot fires in the next ${d.horizon_h || HORIZON_H} hours`));
    spoken.replaceChildren(said.length
      ? el("div", {}, ...said.map(deliveryRow))
      : quiet("nobody spoke — no injection or run-tree hook in the last "
        + `${d.since_h || SINCE_H} hours`));
  }

  async function load() {
    if (inflight || disposed) return;
    inflight = true;
    try {
      paint(await api(`/api/hook-activity?horizon_h=${HORIZON_H}&since_h=${SINCE_H}`));
    } catch {
      // a reading of the stream is never a reason for this page to fail
      if (!fires.firstChild) fires.replaceChildren(el("div", { class: "faint small" },
        "the hook stream could not be read"));
    } finally {
      inflight = false;
    }
  }

  const onBus = (e) => {
    if (e?.detail?.event === "run_finished") load().catch(() => {});
  };

  return {
    node,
    start() {
      if (started) return load();
      started = true;
      window.addEventListener("rsched-bus", onBus);
      timer = setInterval(() => load().catch(() => {}), REFRESH_MS);
      return load();
    },
    dispose() {
      disposed = true;
      window.removeEventListener("rsched-bus", onBus);
      clearInterval(timer);
    },
  };
}
