// Re-read when a RUN FINISHES — and on nothing else the bus carries.
//
// What the development views read (/api/changes, a routine's changes, its recipe health) moves
// only when a run's usage record lands, which is at its finish. So a view re-reads on
// `run_finished` (only its own routine's, given a slug) and on `reconnect` (the stream was down,
// a finish may have been missed), debounced so a lane chain finishing three members in a row
// costs one read. `llm_task` / `llm_process` fire several times a second while a run works and
// can change none of it, so they are never a reason (tests/ui/test_bus_budget.py). The returned
// disposer is the view's teardown: after it, no timer and no listener remain.

const DEBOUNCE_MS = 3000;

export function onRunFinished(reload, { slug = "", delayMs = DEBOUNCE_MS } = {}) {
  let timer = null;
  const onBus = (e) => {
    const ev = e?.detail || {};
    if (ev.event !== "run_finished" && ev.event !== "reconnect") return;
    if (slug && ev.event === "run_finished" && !String(ev.run_id || "").startsWith(`${slug}:`)) return;
    clearTimeout(timer);
    timer = setTimeout(() => { timer = null; reload(); }, delayMs);
  };
  window.addEventListener("rsched-bus", onBus);
  return () => { window.removeEventListener("rsched-bus", onBus); clearTimeout(timer); };
}
