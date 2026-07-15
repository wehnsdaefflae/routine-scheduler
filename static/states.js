// Run-state vocabulary shared across views. TERMINAL = the run is over (nothing live to
// tail); WORKING = the engine is actively producing turns (queued/starting count — the
// user sees "busy"). One source of truth: extend here, never redeclare per view.

export const TERMINAL = new Set(["finished", "failed", "aborted"]);
export const WORKING = new Set(["running", "starting", "queued"]);
