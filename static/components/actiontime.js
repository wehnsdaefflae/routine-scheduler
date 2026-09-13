// Per-action elapsed time compared with the configured operation limit, not a kill countdown.
import { el } from "/static/util.js";

export function actionTime(ev, isLive = () => false) {
  const action = ev.payload || {};
  const defaults = { util: 300, script: 300, shell: 120, wait: 600 };
  const discovery = action.kind === "util" && ["list", "show", "search"].includes(action.name);
  const limit = !discovery && defaults[action.kind]
    ? Number(action.timeout_s || defaults[action.kind]) : null;
  const started = Date.parse(ev.ts);
  const bar = el("progress", { class: "action-time-bar", max: limit || 1, value: 0,
    "aria-label": "Elapsed action time compared with operation timeout" });
  const label = el("span", { class: "action-time-label" });
  const node = el("div", { class: "action-time faint small",
    title: "Elapsed since the action was recorded. Operation limits exclude approval and setup time; this is not a kill countdown." }, bar, label);
  let timer = null;
  let stopped = false;
  const duration = (seconds) => seconds < 60 ? `${Math.floor(seconds)}s`
    : `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
  function draw(end, complete = false) {
    const elapsed = Number.isFinite(started) ? Math.max(0, (end - started) / 1000) : null;
    label.textContent = `${complete ? "completed · " : ""}${elapsed === null ? "time unavailable" : `${duration(elapsed)} elapsed`}`
      + (limit ? ` · ${duration(limit)} operation limit` : " · no fixed action deadline");
    bar.hidden = !limit || elapsed === null;
    if (limit && elapsed !== null) bar.value = Math.min(limit, elapsed);
  }
  function stop(ts, complete = true) {
    stopped = true;
    clearInterval(timer);
    node.classList.remove("running");
    const end = Date.parse(ts);
    draw(Number.isFinite(end) ? end : Date.now(), complete);
  }
  draw(Date.now());
  queueMicrotask(() => {
    if (stopped || !node.isConnected || !isLive()) return;
    node.classList.add("running");
    timer = setInterval(() => {
      if (!node.isConnected || !isLive()) return stop(null, false);
      draw(Date.now());
    }, 250);
  });
  return { node, stop };
}
