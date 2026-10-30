// The finish ACCOUNTING as the console reads it (`engine/accounting.py`) — the two sentences a
// transcript says about it: why a finish was DEFERRED for its accounting, and what a finish
// that stood RECORDED.
//
// Its own module because `transcript.js` is over the repo's 350-line house bar and frozen at its
// ratchet ceiling, so an addition belongs in a new file (CLAUDE.md Standards,
// tests/test_file_size.py) — the same move `obs-background.js` was extracted by. The addition
// that forced it was F748's placeholder category: an entry whose evidence clause answers for
// nothing (`d1 met: test`), which the engine names in full rather than by id, because the point
// of the refusal is to show the model what it wrote.
//
// Every clause here mirrors one key of `accounting.problems`'s `found` dict. A key missing from
// this file renders as NOTHING — a finish deferred for a reason the operator cannot see — so a
// new category in the engine is a change here too.

// A finish deferred for its ACCOUNTING (engine/accounting.problems): the ids no field arrived
// for at all, the ids it left out, the entries with a verdict and no note, the entries whose note
// is a PLACEHOLDER (named in full, as the engine names them), and the verdicts it claimed that
// its line cannot carry.
export function accountingGaps(found) {
  const parts = [];
  // ABSENT is not MISSING (F805): no `accounting` field arrived at all, so the ids are what the
  // finish owed rather than what it got wrong — one run read nine "incomplete" verdicts about
  // entries it could see in its own reply and spent ten turns bisecting them.
  if ((found.absent || []).length) {
    parts.push(`no accounting field arrived — nothing received for ${found.absent.join(", ")}`);
  }
  if ((found.missing || []).length) parts.push(`no verdict for ${found.missing.join(", ")}`);
  if ((found.bare || []).length) parts.push(`no note behind ${found.bare.join(", ")}`);
  if ((found.placeholder || []).length) {
    parts.push(`a placeholder account: ${found.placeholder.join(", ")}`);
  }
  if ((found.refused || []).length) parts.push(found.refused.join("; "));
  return parts.join(" · ") || "an entry did not parse";
}

// What a finish recorded: the verdict it gave each line, the finish-line outcomes it newly
// proved, and where a check of its transcript disagreed — or that the finish line is reached.
export function accountingLine(p) {
  if (p.goal_reached) {
    return "— the finish line is reached: scheduling stops; a Decisions card asks to "
      + "confirm retiring the routine —";
  }
  const judged = Object.entries(p.judged || {}).map(([id, v]) => `${id} ${v}`).join(" · ");
  const met = (p.met || []).length ? ` · proved ${p.met.join(", ")}` : "";
  const disputed = (p.disputed || []).length
    ? ` · a check of the transcript disagreed on ${p.disputed.join(", ")}` : "";
  return `— accounting: ${judged || "nothing owed"}${met}${disputed} —`;
}
