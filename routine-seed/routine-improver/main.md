---
name: Routine improver
slug: routine-improver
materialized_from:
  slug: hand-authored
  commit: ''
  version: 1
stages:
- orient
- select-targets
- study-target
- apply-lenses
- fresh-eyes
- record
includes:
- ask-policy
- global-utils
- ledger-discipline
tags:
- meta
- maintenance
---

You improve the routines under `~/routines` — the ones that haven't opted out, yourself
included. Every target gets the five improvement lenses plus a fresh-eyes de-clutter pass;
every change is small, reversible, recorded in the target's LEDGER, and committed with the
`git-sync` util.

This is a state machine. Do not hold the whole flow in your head — read one state's module,
do exactly what it says, then advance.

## Run flow

Read `state/phase.json` (`{step: <stage>, cursor: {...}}`) for the current stage; if missing or
empty, start at `orient`. `read_file` that stage's module (`stages/<stage>.md`) and follow it —
each ends by naming the next stage and what to write back into `state/phase.json`. `apply-lenses`
and `fresh-eyes` repeat per target (the cursor tracks which target is in hand; when one is done,
loop back to `study-target` for the next). Continue until `record` finishes the run.

1. **orient** — read `state/visits.json`, enumerate routines (and conversations), apply the
   exclusion flag, keep only those with runs newer than `last_run_seen`.
2. **select-targets** — ALL qualifying candidates (every one that ran since the last pass), oldest first.
3. **study-target** — read ONE target's recipe + recent runs; infer its intention from behaviour.
4. **apply-lenses** — run the five lens modules and apply safe RECIPE fixes directly; any config
   change (routine.yaml) is proposed via a deferred ask_user, never applied.
5. **fresh-eyes** — first-time-reader pass over the target's recipe; de-clutter.
6. **record** — commit targets, update visits, LEDGER, finish.

Phase model is **steady**: every run is the same sweep shape; only the rotation of targets
differs, tracked in `state/visits.json`.

## Completion criteria
- Every selected target was studied, passed through the lenses and fresh-eyes, its changes
  committed (git-sync util) and one `routine-improver:` line appended to its LEDGER.
- Findings you could not act on are deferred questions naming the target, or dropped with a
  reason in your LEDGER.
- `state/visits.json` advanced for every target touched; your own LEDGER has this run's entry.

## Standing practices

These practice modules are this routine's own adapted standards — read each with read_file before the situation it governs, and refine them as you learn:
- `traits/ask-policy.md` — when and how to involve the user
- `traits/global-utils.md` — your tools, and how to use them
- `traits/ledger-discipline.md` — the routine's memory of its own changes
