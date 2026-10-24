// The routine's JOBS (rsched/jobs.py, docs/jobs.md), in the overview zone beside its runs:
// every job with its state, whether the current or last run owed it and what came of it, the
// workspace it works in, and the gate checks that watch it. Only for a routine whose job layer
// is on (its Abilities settings) — the run creates, checkpoints and deletes jobs as work comes
// up; this panel is where a person STEERS the list: pause a job, resume it, mark it done.
//
// A change lands between runs only (the server answers 409 while one is live: the run decided
// at boot what it owes, and its finish is gated on exactly that), and the panel says so instead
// of offering a button that would bounce.

import { api } from "/static/api.js";
import { chip, el, toastError, when } from "/static/util.js";

const STATE_CHIP = { active: "idle", paused: "paused", done: "finished" };
const OUTCOME_CHIP = { advanced: "ok", "no-work": "idle", blocked: "paused", deferred: "partial" };

function lastLine(t) {
  const last = t.last;
  if (!last) return el("span", { class: "muted" }, "never processed");
  return el("span", {}, chip(last.outcome, OUTCOME_CHIP[last.outcome] || ""), " ",
    when(last.at, { mode: "rel" }));
}

function runMark(t) {
  if (t.open) return chip("open now", "running");
  if (t.done) return chip(`this run: ${t.done}`, OUTCOME_CHIP[t.done] || "idle");
  if (t.due) return chip("due", "queued");
  return null;
}

export function mountJobs(host, slug, { active = false } = {}) {
  let read = 0;
  let busy = active;

  async function setState(tid, state) {
    try {
      await api(`/api/routines/${slug}/jobs/${encodeURIComponent(tid)}`,
        { method: "PATCH", body: { state } });
      reload();
    } catch (err) { toastError(err); }
  }

  function actions(t) {
    if (busy) return el("span", { class: "muted small" }, "editable between runs");
    const btn = (label, state) => el("button", {
      class: "btn small", "data-job-action": state,
      onclick: () => setState(t.id, state) }, label);
    if (t.state === "active") return el("span", { class: "row" }, btn("pause", "paused"), btn("done", "done"));
    return btn("resume", "active");
  }

  function row(t) {
    const summary = t.last?.summary || "";
    return el("tr", { "data-job": t.id },
      el("td", {},
        // A `<b>`, as the rail's twin of this row renders the same title (runjob.js:61): the
        // class this line carried was `strong`, which NO stylesheet has ever had a rule for
        // (R2885, tests/test_static_class_contract.py), so the job's title — the one thing
        // the row is read for — rendered at body weight like the id line under it.
        el("div", {}, el("b", {}, t.title || t.id)),
        el("div", { class: "muted small mono" }, `${t.id} · ${t.workspace}/`
          + (t.checks?.length ? ` · watched by ${t.checks.join(", ")}` : " · no gate check")
          + (t.origin ? ` · formerly the routine ${t.origin}` : ""))),
      el("td", { class: "inline" }, chip(t.state, STATE_CHIP[t.state] || ""), " ", runMark(t)),
      el("td", { class: "inline" }, lastLine(t)),
      el("td", { class: "muted prose", title: summary,
                 style: "max-width:380px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" },
        t.carry ? `carried: ${t.carry}` : (t.why?.length ? `due: ${t.why[0]}` : summary)),
      el("td", {}, actions(t)));
  }

  function render(data) {
    const items = data.jobs || [];
    const head = el("p", { class: "muted small" },
      items.length
        ? `${items.length} job(s). Every job due in a run is opened and checkpointed before `
          + "that run may finish; what a run never checkpointed is carried to the next."
        : "No jobs yet — the routine creates them with its job action as work comes up.");
    const problems = (data.problems || []).map((p) => el("div", { style: "color:var(--err)" }, `⚠ ${p}`));
    if (!items.length) return el("div", { class: "panel" }, head, ...problems);
    return el("div", { class: "panel", style: "padding:0", "data-jobs-panel": "" },
      el("div", { style: "padding:10px 14px 0" }, head, ...problems),
      el("div", { class: "tablewrap" },
        el("table", { class: "list stack" },
          el("thead", {}, el("tr", {}, ["job", "state", "last checkpoint", "notes", ""]
            .map((h) => el("th", {}, h)))),
          el("tbody", {}, items.map(row)))));
  }

  async function reload({ running } = {}) {
    if (running !== undefined) busy = running;
    const mine = ++read;
    let data;
    try { data = await api(`/api/routines/${slug}/jobs`); }
    catch (err) {
      if (mine === read) host.replaceChildren(el("div", { class: "panel err" }, `jobs could not be read: ${err.message}`));
      return;
    }
    if (mine !== read || !host.isConnected) return;   // only the newest read paints
    host.replaceChildren(render(data));
  }

  reload();
  return { reload };
}
