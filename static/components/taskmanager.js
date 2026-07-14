// The LLM task manager overlay: a global, always-accessible, hideable dock mirroring what LLM
// work is in flight across the whole backend. It owns its own fixed slot (#llm-tasks, a sibling
// of #view) so it survives view navigation, and drives itself off the bus like notify.js —
// window "rsched-bus" for live llm_task / llm_process events + a periodic GET /api/llm-tasks
// reconcile (the bus drops events for a slow subscriber). Nothing here initiates LLM work; it
// is a pure mirror of the backend TaskCenter (the single source of truth).

import { api } from "/static/api.js";
import { el, fmtTokens, storage } from "/static/util.js";

const KEY_OPEN = "llm-tasks-open";           // "1" = panel expanded, else collapsed to the pill
const RECONCILE_MS = 10000;                  // backstop: catch dropped events + prune done items

export function initTaskManager() {
  const slot = document.getElementById("llm-tasks");
  if (!slot) return;

  const processes = new Map();   // id -> {id, kind, label, run_id, closed, error}
  const tasks = new Map();       // id -> {id, status, endpoint, model, purpose, kind, process_id, usage}
  const expanded = new Set();    // process ids the user has un-collapsed (runs collapse by default)
  let open = storage.get(KEY_OPEN) === "1";

  slot.hidden = false;
  const pill = el("button", { class: "lt-pill", type: "button", title: "LLM activity",
    onclick: () => setOpen(!open) });
  const panel = el("div", { class: "lt-panel", hidden: true });
  slot.replaceChildren(pill, panel);

  // ---- live model upkeep ----------------------------------------------------
  function applyTask(ev) {
    const t = tasks.get(ev.id) || {};
    Object.assign(t, ev);   // ev carries id/status/endpoint/model/purpose/kind/process_id/usage
    tasks.set(ev.id, t);
  }
  function applyProcess(ev) {
    if (ev.phase === "opened") {
      processes.set(ev.id, { id: ev.id, kind: ev.kind, label: ev.label, run_id: ev.run_id,
        closed: false });
      if (ev.kind === "run") expanded.delete(ev.id);   // runs collapse by default (many turns)
      else expanded.add(ev.id);                        // wizard/recompile: show children by default
    } else if (ev.phase === "closed") {
      const p = processes.get(ev.id);
      if (p) { p.closed = true; if (ev.error) p.error = ev.error; }
    }
  }
  function onBus(e) {
    const ev = e.detail || {};
    if (ev.event === "llm_task") applyTask(ev);
    else if (ev.event === "llm_process") applyProcess(ev);
    else return;
    schedule();
  }

  async function reconcile() {
    try {
      const snap = await api("/api/llm-tasks");
      processes.clear();
      for (const p of snap.processes || []) processes.set(p.id, p);
      tasks.clear();
      for (const t of snap.tasks || []) tasks.set(t.id, t);
      // prune expand-state for processes that no longer exist
      for (const id of [...expanded]) if (!processes.has(id)) expanded.delete(id);
      render();
    } catch { /* the daemon lamp already reports connectivity */ }
  }

  // ---- rendering (debounced) ------------------------------------------------
  let timer = null;
  function schedule() {
    if (timer) return;
    timer = setTimeout(() => { timer = null; render(); }, 80);
  }

  function running() {
    let n = 0;
    for (const t of tasks.values()) if (t.status === "running") n++;
    return n;
  }

  function setOpen(v) {
    open = v;
    storage.set(KEY_OPEN, v ? "1" : "0");
    render();
  }

  function statusDot(status) {
    return el("span", { class: `lt-dot ${status || "running"}`, "aria-hidden": "true" });
  }

  function taskRow(t) {
    const meta = [t.endpoint, t.model].filter(Boolean).join(" / ");
    return el("div", { class: `lt-task ${t.status || "running"}` },
      statusDot(t.status),
      el("div", { class: "lt-task-body" },
        el("div", { class: "lt-purpose" }, t.purpose || "LLM call"),
        el("div", { class: "lt-meta" },
          meta ? el("span", { class: "lt-model" }, meta) : null,
          t.usage ? el("span", { class: "lt-usage" }, fmtTokens(t.usage)) : null,
          t.error ? el("span", { class: "lt-err" }, t.error) : null)));
  }

  function processRow(p, children) {
    const done = children.filter((c) => c.status !== "running").length;
    const anyErr = p.error || children.some((c) => c.status === "error");
    const state = !p.closed ? "running" : anyErr ? "error" : "done";
    const isOpen = expanded.has(p.id);
    const head = el("div", { class: `lt-proc-head ${state}`,
      onclick: () => { isOpen ? expanded.delete(p.id) : expanded.add(p.id); render(); } },
      el("span", { class: "lt-caret" }, isOpen ? "▾" : "▸"),
      statusDot(state),
      el("span", { class: "lt-proc-label" }, p.label || p.id),
      el("span", { class: "lt-proc-count" }, `${done}/${children.length}`));
    const rows = [head];
    if (isOpen) rows.push(el("div", { class: "lt-proc-children" }, children.map(taskRow)));
    return el("div", { class: "lt-proc" }, rows);
  }

  function render() {
    const runN = running();
    pill.hidden = open;      // pill and panel are mutually exclusive — one handle at a time
    panel.hidden = !open;
    if (!open) {
      pill.className = `lt-pill${runN ? " active" : ""}`;
      pill.replaceChildren(el("span", { class: "lt-bolt", "aria-hidden": "true" }, "⚡"),
        el("span", { class: "lt-pill-n" }, runN ? String(runN) : "LLM"));
      return;
    }

    // group tasks under their process; the rest are standalone one-offs
    const byProc = new Map();
    const standalone = [];
    for (const t of tasks.values()) {
      if (t.process_id && processes.has(t.process_id)) {
        if (!byProc.has(t.process_id)) byProc.set(t.process_id, []);
        byProc.get(t.process_id).push(t);
      } else {
        standalone.push(t);
      }
    }
    const body = [];
    for (const p of processes.values()) body.push(processRow(p, byProc.get(p.id) || []));
    for (const t of standalone) body.push(taskRow(t));

    panel.replaceChildren(
      el("div", { class: "lt-head" },
        el("span", { class: "lt-title" }, "LLM activity"),
        el("span", { class: "lt-head-n" }, runN ? `${runN} running` : "idle"),
        el("button", { class: "lt-close", type: "button", title: "collapse",
          onclick: () => setOpen(false) }, "–")),
      body.length ? el("div", { class: "lt-list" }, body)
        : el("div", { class: "lt-empty" }, "No LLM calls in flight."));
  }

  window.addEventListener("rsched-bus", onBus);
  render();
  reconcile();
  setInterval(reconcile, RECONCILE_MS);
}
