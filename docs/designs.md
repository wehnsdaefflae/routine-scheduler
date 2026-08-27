# Designs not yet built

Specs for work that is **decided but unbuilt**. Each entry states the problem from real
evidence, the shape, and the FIRST increment — enough that whoever picks it up is not
re-deriving it. An entry is deleted the moment it ships (its narration moves to the
subsystem doc it belongs to); an entry that stops being wanted is deleted too. Nothing here
describes current behaviour, so nothing here is a reference for how the system works today.

Written 2026-08-26 against 0.230.x, on the operator's order to clear the queued design
backlog. Item ids are the Messages-page findings they answer.

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
