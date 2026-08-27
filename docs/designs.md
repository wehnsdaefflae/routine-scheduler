# Designs not yet built

Specs for work that is **decided but unbuilt**. Each entry states the problem from real
evidence, the shape, and the FIRST increment — enough that whoever picks it up is not
re-deriving it. An entry is deleted the moment it ships (its narration moves to the
subsystem doc it belongs to); an entry that stops being wanted is deleted too. Nothing here
describes current behaviour, so nothing here is a reference for how the system works today.

Started 2026-08-26 against 0.230.x, on the operator's order to clear the queued design
backlog. An entry headed by an item id answers that Messages-page finding; an entry
decided in conversation before any finding exists says so and carries none.

---

## F363 — failover support: a per-stage distillate

**Problem.** A catalog model may declare `fallbacks:` (endpoints/failover.py). When the
primary fails hard the run continues on a weaker model — with the *same* prompt. The recipe
was written for the strong model: it says WHAT to do and leaves the how to judgment, which
is exactly what the weaker model does not have. So failover keeps the run alive and quietly
degrades the work.

**Decision (D95, operator 2026-08-20, free text; recommendation A).** Support the fallback
model with a per-STAGE *distillate* — a short, concrete crib for that one stage — injected
**only when failover has engaged**. Not a second recipe, and never in the normal prompt.

**Shape.**

- A distillate is one file per stage: `<routine>/stages/<name>.distillate.md`, ≤ ~25 lines.
  It holds what the strong model actually DID in that stage, reduced to instructions a
  weaker model can follow: the concrete tool calls in order, the shape of the output, the
  two or three traps previous runs hit. It names capabilities, never utils — the
  recipe rule (CLAUDE.md) applies to it unchanged.
- **Derivation is a meta-routine's job, not the engine's.** routine-improver already reads a
  routine's own finished runs; it writes the distillate from runs that (a) completed on the
  routine's `main` model and (b) reached that stage. A stage with fewer than N such runs gets
  no distillate — an invented crib is worse than none.
- **Injection.** `engine/completion.py` already logs the failover switch as a transcript
  `error` event carrying a `failover` payload. That is the trigger: from the moment a run
  switches down the chain, the composer appends the CURRENT stage's distillate to the turn's
  context, under a heading that says plainly what it is ("You are running on a fallback
  model. Here is how this stage was carried out on the primary."). It is dropped again if
  the run climbs back to the primary.
- **The caching contract holds** (CLAUDE.md: the message list is appended-to, never mutated).
  Failover already invalidates the provider cache by switching model, so the append costs
  nothing extra — but it must be an APPEND at the switch, not a per-turn re-render.

**Why not the alternatives.** A whole second recipe doubles what routine-improver maintains
and drifts silently. Injecting the distillate always makes every strong-model run read a crib
written for a weaker one — the failure mode that made `deliberation` a knob in the first
place.

**First increment.** The injection seam only, with distillates written by hand for one
routine: teach the composer to append `stages/<current>.distillate.md` when the run's
transcript carries a `failover` event. That is testable with `ScriptedEndpoint` and proves
the trigger before any generation work exists.

---

## Progressive ossification — the run history as the routine's own corpus

**Decision** (operator, 2026-08-27, in conversation). No item id yet: this entry precedes
its finding rather than answering one. F363 is the same operation at a different
compression level — take the corpus of prior runs of ONE stage, emit an artifact that
reduces what the next run must work out. F363 emits prose for a weaker model; this emits
code, or nothing at all, for no model.

**Problem.** A routine re-pays the same deliberation every run for steps whose answer never
changes — which files to read, which parameters to fetch with, which capability to reach
for. That is not one turn: it is the several turns of context-acquisition that precede the
action, all to arrive at a conclusion the last twenty runs already reached. The system
already wants this fixed — `scripts/` exists as the ossification target, and
routine-improver scouts recipes for deterministic prose responsibilities — but nothing
MEASURES whether a step has converged. Today an ossification is the improver's taste, and
a wrong one degrades the work silently, because a mature routine is the one nobody reads.

**The direction, which is the load-bearing decision.** Exploration is the STARTING state
and exploitation is earned; never the reverse. A scheme with reuse as the default and a
sampling rate for fresh deliberation makes exploration permanent rent — the cost curve
flattens instead of falling. Here the unit of progress is the STEP, not the run: every step
begins in prose, evidence accrues from ordinary runs at zero extra cost (that deliberation
was happening anyway), and a step is promoted only when its own history earns it. A routine's
cost is then monotonically decreasing over its lifetime.

**Shape.**

- **One corpus, many consumers.** Per routine, per stage: the cases `(preceding
  observation → action taken, with what it cost)` across all runs. Derived, never written.
  Everything wants it — F363's derivation half, ossification into `scripts/`, per-stage
  model right-sizing, budget and cadence calibration from measured distributions, an
  anomaly alarm, convergence detection. Build the corpus, not N features.
- **A promotion ladder, each rung demanding more evidence:** prose → the run reads what
  previous runs did here and re-issues it itself → a guarded reuse → a script. The first
  rung needs no engine change and no new action kind: the model reads the cases and
  chooses, so it is the guard on every use, and its disagreement is exploration for free.
- **The promotion test is that the corpus is a FUNCTION** — same input class, same action,
  across N cases. Variance in the decision IS the judgment, and an unformalizable step must
  stay in prose. This is what makes "formalizable" checkable rather than a vibe. A run that
  freely re-issues an identical action, having been able to choose otherwise, is the
  strongest evidence available that the step is constant — and it is generated during
  normal operation, so no shadow-execution machinery is needed.
- **Rank candidates by DELIBERATION cost, not execution cost.** There is no reasoning-token
  field (usage is `{in, out, cost}` per event), but reasoning bills as output: a turn with a
  two-line action and a large `out` is a heavy-deliberation turn. `out ÷ (say + action size)`
  ranks candidates with no new instrumentation. Multiply by frequency and by constancy.
- **Demotion, not sampling.** A reused decision still EXECUTES, so its observation returns
  to the run, which reads it fresh: a fetch that returns garbage, a parse that fails, a file
  that is gone. Drift is caught by failure, not by a schedule. `scripts.py` already returns
  `(exit, out, err)` into the observation, so a script that raises on unrecognized input
  hands the step back to prose by itself — the discipline is writing scripts that raise
  rather than guess.
- **A step whose failure would be SILENT is the only residual risk.** The fix there is to
  make the failure loud (an assertion over the output shape, which prior runs hand you for
  free), never to re-deliberate periodically.
- **What this dissolves:** cache-invalidation fingerprints. A changed stage is not a stale
  cache, it is a NEW step with no accumulated evidence, which starts in prose like anything
  else. Do not build a scheme that tracks every input to a decision (stage text, permissions,
  rules, model, util catalog): it is bookkeeping that still cannot see the outside world
  change, which is the common case.

**Why not the alternatives.** A global exploration rate charges rent forever and never lets
a converged step settle. A `replay`/`reuse` action kind FIRST inverts the value: the actions
safe to replay are cheap to redo anyway, and the ones worth replaying carry effects — while
the corpus-as-read version needs no 26th kind at all. A dedicated kind earns its place only
once the corpus shows a stage where the run always agrees AND the arguments are bulky enough
that emitting them is the cost.

**First increment.** The per-stage, cross-run CASE INDEX — nothing that consumes it. Half of
it exists: every `assistant_action` is stamped with the active phase, and `statemap.phase_stats`
already derives per-phase turns / tokens / cost from one transcript. What is missing is the
cross-run aggregation keyed by stage, holding cases rather than totals. Build it on the
`readmodels/util_stats.py` pattern — retained transcripts, both homes, root + sub, memoized
behind an (inode, mtime, size) fingerprint, no database.

**The one trap that shape carries:** transcripts are PRUNED. `util_stats` needed a durable
usage stream precisely because retention would otherwise erase its history, and a corpus that
forgets is worst exactly where it matters — a step converges over the long tail, which is the
part retention deletes first. So the case index needs its own durable record from day one, or
it will confidently report convergence over whatever window happens to survive.

---
