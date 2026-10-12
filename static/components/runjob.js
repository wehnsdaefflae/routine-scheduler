// The run rail's JOBS section: which of the routine's jobs THIS run worked, and which one it is
// working on right now (rsched/jobs.py, docs/jobs.md). Operator order (F646): "routines with
// tasks (like the 'fau' routine) need to display the task they're currently working on in the
// right sidebar" — the run page is where a run is actually watched, and until now the open job
// was visible only on the ROUTINE page, which shows the store rather than this run.
//
// It reads `GET /api/routines/{slug}/jobs`, the same read the routine page's panel makes: the
// store already carries everything the rail needs — the ledger's open job for the live run, the
// per-job outcome this run recorded, and each job's own history for a run that has ended. The
// run's `status.json` carries only the open job's ID and the engine CLEARS it at close-out, so
// it is the wrong source for a page that is read long after the run.
//
// Two shapes, by whose ledger it is:
//   - the ledger names THIS run → the open job leads, then what is still due, then what this
//     run checkpointed;
//   - an older run → each job whose history holds an entry for this run id, with the outcome it
//     recorded then. A job checkpointed more than five times since is no longer in the served
//     history and is simply not claimed.
//
// Like the oversight strip, the section is ADDED only once there is something in it, and its
// caption is `jobs` — the same word the routine page's panel uses, so one vocabulary names the
// layer on both surfaces. A routine without the job layer gains no caption at all: an
// always-present, always-empty rail card teaches a reader to skip the section.

import { api } from "/static/api.js";
import { chip, el } from "/static/util.js";

const STATE_CHIP = { active: "idle", paused: "paused", done: "finished" };
const OUTCOME_CHIP = { advanced: "ok", "no-work": "idle", blocked: "paused", deferred: "partial" };
// open first — "what is it doing NOW" is the question this section exists to answer.
const RANK = { open: 0, due: 1, done: 2 };

/** What this run has to do with each job: `open` / `due` / `done`, or nothing at all. */
function forThisRun(data, runId) {
  const ledger = data.run || {};
  const mine = ledger.run_id === runId;
  const rows = [];
  for (const job of data.jobs || []) {
    if (mine) {
      if (job.open) rows.push({ job, mark: "open", brief: job.brief, note: job.carry });
      else if (job.done) rows.push({ job, mark: "done", outcome: job.done,
                                     note: job.last?.summary });
      else if (job.due) rows.push({ job, mark: "due", note: (job.why || [])[0] });
    } else {
      const was = (job.history || []).find((h) => h.run === runId);
      if (was) rows.push({ job, mark: "done", outcome: was.outcome, note: was.summary });
    }
  }
  return rows.sort((a, b) => RANK[a.mark] - RANK[b.mark]);
}

function markChip(r) {
  if (r.mark === "open") return chip("working on it now", "running");
  if (r.mark === "due") return chip("due this run", "queued");
  return chip(r.outcome || "checkpointed", OUTCOME_CHIP[r.outcome] || "");
}

function jobNode(r) {
  const job = r.job;
  const kids = [
    el("div", { class: "rj-title" }, el("b", {}, job.title || job.id)),
    el("div", { class: "small" }, markChip(r), " ",
      chip(job.state, STATE_CHIP[job.state] || "")),
    el("div", { class: "rj-where faint small mono" }, `${job.id} · ${job.workspace || ""}`),
  ];
  // The note is the one line that says what this job IS about: the brief while it is open, the
  // checkpoint summary once it is not, the carry when something was left owed. Full text on
  // hover — the rail is narrow and a brief is a paragraph.
  if (r.note) kids.push(el("div", { class: "faint small prose", title: r.note }, r.note));
  else if (r.mark === "open" && r.brief) {
    kids.push(el("div", { class: "faint small prose", title: r.brief }, r.brief));
  }
  return el("div", { "data-run-job": job.id, "data-job-mark": r.mark }, ...kids);
}

/**
 * Mount the rail's jobs section. `rail` is the shared rail component, `runId` this run's id and
 * `isLive` the view's own liveness predicate. Returns `{ refresh, stop }`; nothing is added to
 * the rail until a read finds a job this run has something to do with.
 */
export function mountRunJob(rail, { slug, runId, isLive }) {
  let body = null;
  let timer = null;
  let stopped = false;
  let loadedOnce = false;
  const host = el("div", { class: "runjob" });

  function paint(rows) {
    if (!rows.length) {
      if (body) rail.toggle("jobs", false);
      return;
    }
    if (!body) {
      body = rail.add("jobs", host);
    } else {
      rail.toggle("jobs", true);
    }
    host.replaceChildren(...rows.map(jobNode));
  }

  async function refresh() {
    let data;
    try {
      data = await api(`/api/routines/${slug}/jobs`);
    } catch {
      // A first read that fails says nothing: the overwhelming majority of routines keep no
      // jobs, so a card reading "jobs could not be read" would be noise on almost every run
      // page. Once the section exists, a failed refresh keeps the last render instead.
      return;
    }
    loadedOnce = true;
    if (stopped) return;
    paint(data.enabled === false ? [] : forThisRun(data, runId));
  }

  // The poller is self-defending (subruntree.js holds the same guard): `isLive` reads a state
  // variable frozen at whatever it held when the view was torn down, so a forgotten stop()
  // would leave a GET running for the rest of the browser session.
  let wasLive = !!(isLive && isLive());
  function poll() {
    if (timer) clearTimeout(timer);
    timer = null;
    if (stopped || !host.isConnected) return;
    const live = !!(isLive && isLive());
    // While the run works, the ledger changes under it — a job opens, another is checkpointed.
    // Once it ends, ONE more read: close-out clears the open job and carries what was never
    // checkpointed, so a rail frozen at the last live poll would go on saying "working on it
    // now" about a run that has finished.
    if (!live && !wasLive) return;
    wasLive = live;
    timer = setTimeout(async () => {
      if (stopped || !host.isConnected) return;
      await refresh();
      poll();
    }, live ? 5000 : 1500);
  }

  refresh().then(poll);
  return {
    refresh() { if (!stopped) { refresh().then(() => { if (loadedOnce) poll(); }); } },
    stop() { stopped = true; if (timer) clearTimeout(timer); timer = null; },
  };
}
