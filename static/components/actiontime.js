// Per-action elapsed time compared with the configured operation limit, not a kill countdown —
// and, for a call that is still running, the one control that STOPS it (F586, decided as D160-C).
//
// The cancel lives here rather than on the action row because this is the component that already
// knows the two things it needs: whether the view is LIVE (`isLive`) and whether this particular
// call is still running (`stop()` has not been called for it). A button rendered from anywhere
// else would have to re-derive both and would go stale the moment the observation arrived.
import { el } from "/static/util.js";

//: The kinds a cancel can reach: the three that run a jailed child process the engine polls
//: while it waits (`utils_run.run_jailed`). Everything else is engine work inside the turn
//: itself — there is no process to stop, and offering the control would promise one.
const CANCELLABLE = ["util", "script", "shell"];

export function actionTime(ev, isLive = () => false, cancelAction = null) {
  const action = ev.payload || {};
  const defaults = { util: 300, script: 300, shell: 120, wait: 600 };
  const discovery = action.kind === "util" && ["list", "show", "search"].includes(action.name);
  const limit = !discovery && defaults[action.kind]
    ? Number(action.timeout_s || defaults[action.kind]) : null;
  const started = Date.parse(ev.ts);
  const bar = el("progress", { class: "action-time-bar", max: limit || 1, value: 0,
    "aria-label": "Elapsed action time compared with operation timeout" });
  const label = el("span", { class: "action-time-label" });
  // The turn is what the cancel travels as (the endpoint keys it, so a cancel can never land on
  // the NEXT call), which means a row without a turn cannot offer the control at all.
  const turn = Number.isFinite(Number(ev.turn)) ? Number(ev.turn) : null;
  // `isLive()` is answerable right now, so a REPLAYED row never builds the control at all
  // rather than building it hidden. Absent is a stronger guarantee than hidden: a hidden button
  // is still in the DOM, still in the keyboard focus order, and still one `hidden = false` away
  // from offering a cancel for a turn that ended weeks ago. (The run rail learned the same thing
  // the hard way — `rail.toggle(name, false)` only sets `cap.hidden` and the element stayed.)
  const cancelable = cancelAction && turn !== null && CANCELLABLE.includes(action.kind)
    && isLive();
  const cancel = cancelable
    ? el("button", { class: "action-cancel", type: "button", hidden: true,
        title: `Stop this ${action.kind} call (turn ${turn}). The run itself carries on.`,
        "aria-label": `Cancel the ${action.kind} call of turn ${turn}` }, "✕")
    : null;
  const node = el("div", { class: "action-time faint small",
    title: "Elapsed since the action was recorded. Operation limits exclude approval and setup time; this is not a kill countdown." }, bar, label, cancel);
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
    // The call is over, however it ended: the control goes away rather than lingering as a
    // button that would now key a turn the run has passed.
    if (cancel) cancel.hidden = true;
    const end = Date.parse(ts);
    draw(Number.isFinite(end) ? end : Date.now(), complete);
  }
  if (cancel) {
    cancel.addEventListener("click", async () => {
      cancel.disabled = true;
      cancel.textContent = "…";
      try {
        await cancelAction(turn);
        // The engine polls control.json every quarter second, so the call ends shortly after
        // this returns — not at this instant. Say that, instead of claiming it is already done.
        cancel.textContent = "✕";
        cancel.hidden = true;
        label.textContent = `cancel sent for turn ${turn} — stopping…`;
      } catch (err) {
        // A failed cancel must not look like a successful one: the button comes back so the
        // operator can try again, and the reason is on the row rather than in the console.
        cancel.disabled = false;
        cancel.textContent = "✕";
        label.textContent = `cancel failed: ${err?.message || err} · ${label.textContent}`;
      }
    });
  }
  draw(Date.now());
  queueMicrotask(() => {
    if (stopped || !node.isConnected || !isLive()) return;
    node.classList.add("running");
    // Only now: a row rendered from history is not a running call, and a × on it would key a
    // turn that is long past.
    if (cancel) cancel.hidden = false;
    timer = setInterval(() => {
      if (!node.isConnected || !isLive()) return stop(null, false);
      draw(Date.now());
    }, 250);
  });
  return { node, stop };
}
