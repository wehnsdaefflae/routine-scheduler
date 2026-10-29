// The engine's NEUTRAL RECORDS about a run, in one sentence each — what happened TO the run
// rather than what the model did: the context compacted or archived, an oversight rung firing,
// a call a person stopped.
//
// Its own module for the reason obs-background.js, obs-accounting.js and obs-text.js are
// theirs: `static/components/transcript.js` is over the repo's 350-line house bar and frozen at
// its ratchet ceiling, so an addition belongs in a new file (CLAUDE.md Standards,
// tests/test_file_size.py). The responsibility is one the transcript's own prose already named
// — "a neutral line, like compaction: it records a state change the reader needs to see in
// place, not an action the model took" — and these rows share a failure mode that is not the
// per-kind chain's: each is a terse line assembled from optional payload fields, so a missing
// field reads as a confident "undefined" rather than as an error. Every `?? "?"` below is one
// of those, and the compaction branches exist because the row once said
// "undefined → undefined chars".
//
// PURE: payload in, string out. transcript.js keeps the `SIMPLE` renderer table itself (its
// keys are what tests/test_static_transcript.py reads to prove every EVENT_TYPES member has a
// renderer) and the class each row carries, which the stylesheet keys off.

/** The compaction line: the pass that happened, with only the numbers the payload has. */
export function compactionText(payload) {
  // Three payload shapes, one line each (never "undefined → undefined"): the LLM
  // archive / deterministic digest (flat before/after), the hard window clamp
  // ({clamp}), and the provider-window correction ({window_guard}, optional clamp).
  const p = payload || {};
  const c = p.clamp || p.window_guard?.clamp || null;
  const span = (o) => o.before_estimated_tokens != null
    ? `${o.before_estimated_tokens} → ${o.after_estimated_tokens} estimated tokens`
    : `${o.before_chars} → ${o.after_chars} chars`;
  let text;
  if (p.window_guard) {
    const g = p.window_guard;
    text = `— window corrected: ${g.model} really holds ${g.corrected_tokens != null ? `${g.corrected_tokens} tokens` : `${g.corrected_chars} chars`}` +
           `${c ? `; clamped ${c.clamped_messages} oversized, ${span(c)}` : ""} —`;
  } else if (c) {
    text = `— window clamp: ${c.clamped_messages} oversized ` +
           `${c.clamped_messages === 1 ? "body" : "bodies"} trimmed in place, ${span(c)} —`;
  } else if (p.archival_abandoned) {
    // the run ended before the thread did — a designed outcome, not a loss
    text = `— background archive abandoned: the run ended before the ${p.elided_messages} `
         + `elided messages finished archiving; the digest and the full transcript stand —`;
  } else if (p.background) {
    // The archive that landed OFF the hot path (0.308.0). It carries no before/after
    // chars — the digest already took the pass and did the shrinking — so it must not
    // reach the branches that print a span, which is how it once read "undefined →
    // undefined chars".
    text = p.archival_degraded
      ? "— background archive did not land —"
      : `— background archive landed: ${p.elided_messages} messages → history/ `
        + `(${p.history_files} files, browsable in the rail's files card) —`;
  } else if (p.mode === "llm-history") {
    text = `— context archived: ${p.elided_messages} messages → history/ ` +
           `(${p.history_files} files, browsable in the rail's files card), ${span(p)} —`;
  } else if (p.before_estimated_tokens != null || p.before_chars != null) {
    text = `— context compacted: ` +
           `${p.elided_messages ? `${p.elided_messages} messages digested, ` : ""}${span(p)} —`;
  } else {
    // archival failed AND the digest had nothing to elide — reason-only record
    text = `— compaction: nothing elided this pass —`;
  }
  if (p.archival_degraded) {
    // a failed LLM archival is a designed degrade (the digest took the pass), not a
    // run error (F376) — note it on the neutral line instead of a red error card
    text += ` (history archival degraded: ${p.archival_degraded})`;
  }
  return text;
}

/** A finish that STOOD while declared stages went unentered (engine/finishgate.py, F521) —
 * a notice, not a refusal: skipping a stage is often right, and the gap is on the record. */
export function stagesSkippedText(p) {
  return `— stages skipped: ${(p?.skipped || []).join(", ")} `
    + `(entered: ${(p?.entered || []).join(", ") || "none"}) —`;
}

/** The escalation ladder's four records (engine/ladder.py), one sentence each.
 *
 * An event type with no renderer is DROPPED by the transcript's `add()`, so a rung that fired
 * left no trace a reader could find — which for an oversight mechanism is the one failure that
 * must never be silent. Each of the four says something the others do not, and the distinction
 * that cost the most to learn is `failed` vs `skipped`: until R2348/R2352 a total failure
 * (`loop.subs`, an attribute that never existed, on EVERY run) was filed as `oversight_skipped`
 * and read as the ordinary ceiling case, so the transcript showed supervision declining to run
 * where the mechanism was broken.
 */
export const OVERSIGHT_TEXT = {
  // A rung STARTING: why it fired, the interval it judges, and the budget it was given — the
  // reader's only way to see that supervision cost turns and over what.
  dispatch: (p) => `— ↑ rung ${p?.rung ?? "?"} fired at turn ${p?.turn ?? "?"}`
    + ` (${p?.reason || "interval"}), judging turns `
    + `${p?.since_turn ?? "?"}–${p?.turn ?? "?"}`
    + ` on ${p?.oversight_turns ?? "?"} turns of its own —`,
  // The VERDICT; the directive's prose arrives as an ordinary `user_injection`.
  directive: (p) => `— ↑ rung ${p?.rung ?? "?"}: ${p?.verdict ?? "?"}`
    + ` · ${p?.disposition ?? "?"} · next rung in ${p?.next_rung_in ?? "?"} turns`
    + `${p?.next_look ? ` · next look: ${p.next_look}` : ""} —`,
  // Not an error: the ordinary case on a tree at its depth ceiling or out of child budget, and
  // the hard skip when the supervisor's workflow pattern is absent. The REASON is the whole
  // point of the record.
  skipped: (p) => `— ↑ oversight${p?.rung ? ` rung ${p.rung}` : ""} skipped: `
    + `${p?.reason || "no reason recorded"} —`,
  // The mechanism RAISED — not a rung declining to run. The caller draws it in the ERROR
  // palette, which is the whole reason the event is its own type.
  failed: (p) => `— ↑ oversight${p?.rung ? ` rung ${p.rung}` : ""} FAILED: `
    + `${p?.error || "no error recorded"} —`,
  // A rung that ran and handed back no usable directive — supervision that cost turns and said
  // nothing. Visible, because it reads like `continue` and is not.
  none: (p) => `— ↑ rung ${p?.rung ?? "?"} handed back no directive`
    + `${p?.status ? ` (child ${p.status})` : ""} —`,
};

/** A person stopped ONE call (F586 / D160-C).
 *
 * The record exists precisely so a reader afterwards can tell this apart from a call that died
 * on its own, which a transcript of outcomes alone cannot show. Nothing failed, so the caller
 * draws it in the injection palette — somebody outside the run reaching in.
 */
export function actionCancelledText(p) {
  return `— ✕ cancelled by the user: ${p?.kind ?? "?"}`
    + `${p?.brief ? ` · ${p.brief}` : ""} —`;
}

/** The run's header line. Every half falls back: the model one printed a literal
 * "undefined:undefined" on any header without an orchestrator block (a transcript from before
 * the field, or one cut short before the run resolved a model). */
export function headerText(ev) {
  return `run ${ev.run_id} · ${ev.orchestrator?.endpoint || "?"}:${ev.orchestrator?.model || "?"}`
    + ` · workflow ${ev.workflow?.slug || "?"}`;
}
