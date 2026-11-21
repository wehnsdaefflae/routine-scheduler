// What the run is ACTUALLY waiting on, in words (F170).
//
// The engine emits `assistant_action` when a turn's action starts executing and `observation`
// when it lands — BETWEEN THE TWO the run waits on the ACTION (a util, a child run, a file
// op), not on the model. A spinner saying "waiting for the model…" through a 20-minute util
// call is the console telling the reader the wrong thing about where the time is going.
//
// Its own module because `static/views/run.js` is over the repo's 350-line house bar and
// frozen at its ratchet ceiling: a file at its bar pays for a new responsibility by moving one
// out, and this one is pure — an action payload in, a sentence out, no DOM, no state.

/** The sentence for the action currently in flight; `null`/`{}` means the model has the turn. */
export function waitingLabel(pending) {
  const p = pending;
  if (!p || !p.kind) return "waiting for the model…";
  if (p.kind === "util") return p.name ? `running util ${p.name}…` : "running a util…";
  if (p.kind === "llm") return "running an LLM subcall…";
  if (["child", "detach", "wait"].includes(p.kind)) return "waiting on child runs…";
  if (p.kind === "ask_user") return "waiting for your answer…";
  return `executing ${p.kind}…`;
}
