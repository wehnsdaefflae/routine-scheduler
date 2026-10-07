// The routine's TASKS (rsched/tasks.py, docs/tasks.md), in the overview zone beside its runs:
// every task with its state, whether the current or last run owed it and what came of it, the
// workspace it works in, and the gate checks that watch it. Only for a routine whose task layer
// is on (its Abilities settings) — the run creates, checkpoints and deletes tasks as work comes
// up; this panel is where a person STEERS the list: pause a task, resume it, mark it done.
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

export function mountTasks(host, slug, { active = false } = {}) {
  let read = 0;
  let busy = active;

  async function setState(tid, state) {
    try {
      await api(`/api/routines/${slug}/tasks/${encodeURIComponent(tid)}`,
        { method: "PATCH", body: { state } });
      reload();
    } catch (err) { toastError(err); }
  }

  function actions(t) {
    if (busy) return el("span", { class: "muted small" }, "editable between runs");
    const btn = (label, state) => el("button", {
      class: "btn small", "data-task-action": state,
      onclick: () => setState(t.id, state) }, label);
    if (t.state === "active") return el("span", { class: "row" }, btn("pause", "paused"), btn("done", "done"));
    return btn("resume", "active");
  }

  function row(t) {
    const summary = t.last?.summary || "";
    return el("tr", { "data-task": t.id },
      el("td", {},
        el("div", { class: "strong" }, t.title || t.id),
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
    const items = data.tasks || [];
    const head = el("p", { class: "muted small" },
      items.length
        ? `${items.length} task(s). Every task due in a run is opened and checkpointed before `
          + "that run may finish; what a run never checkpointed is carried to the next."
        : "No tasks yet — the routine creates them with its task action as work comes up.");
    const problems = (data.problems || []).map((p) => el("div", { style: "color:var(--err)" }, `⚠ ${p}`));
    if (!items.length) return el("div", { class: "panel" }, head, ...problems);
    return el("div", { class: "panel", style: "padding:0", "data-tasks-panel": "" },
      el("div", { style: "padding:10px 14px 0" }, head, ...problems),
      el("div", { class: "tablewrap" },
        el("table", { class: "list stack" },
          el("thead", {}, el("tr", {}, ["task", "state", "last checkpoint", "notes", ""]
            .map((h) => el("th", {}, h)))),
          el("tbody", {}, items.map(row)))));
  }

  async function reload({ running } = {}) {
    if (running !== undefined) busy = running;
    const mine = ++read;
    let data;
    try { data = await api(`/api/routines/${slug}/tasks`); }
    catch (err) {
      if (mine === read) host.replaceChildren(el("div", { class: "panel err" }, `tasks could not be read: ${err.message}`));
      return;
    }
    if (mine !== read || !host.isConnected) return;   // only the newest read paints
    host.replaceChildren(render(data));
  }

  reload();
  return { reload };
}
