# Study one target

Take the next target from `cursor.targets`. Understand what it is FOR and what it actually
DID before touching anything.

## Do
1. Read the target's recipe — `main.md`, the `stages/` modules it routes to, its `traits/`
   — and its `LEDGER.md` tail (last ~40 lines — earlier improvement attempts and rejected
   candidates live there; never re-apply a known-bad change). A routine carries no
   `instruction.md`: the recipe IS the task. A conversation target additionally has its
   seed `instruction.md` — read that too.
2. Read its most recent run evidence, newest first, at most ~3 runs: `runs/<ts>/status.json`
   for outcome/turns/tokens, and the transcript when the status raises questions (budget
   exhaustion, failures, schema-retry storms). For a big transcript, `spawn` a sub-workflow
   to summarize it against this rubric: outcome; wasted turns; questions asked vs answered;
   places the run contradicted its own workflow text.
3. **Infer the routine's intention from that behaviour** — not only from a fresh reading of
   the recipe. Note where behaviour and recipe disagree: that gap is where the
   best findings live.
4. Write a compact brief into `cursor.brief`: intention, evidence bullets, oddities.

## Next
Write `state/phase.json = {phase: "apply-lenses", cursor: {...}}`. Read `stages/apply-lenses.md`.
