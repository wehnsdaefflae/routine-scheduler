---
name: Routine improver
slug: routine-improver
materialized_from:
  slug: routine-improver
  commit: seed
  version: 1
modules:
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

1. Read `state/phase.json` (`{step: <name>, cursor: {...}}`). If it is missing or empty,
   start at `orient`.
2. `read_file` the module for the current step from `steps/<step>.md` and follow it. Each
   module ends by telling you the next step and what to write back into `state/phase.json`.
3. `apply-lenses` and `fresh-eyes` repeat per target: the cursor tracks which target is in
   hand; when one target is done, loop back to `study-target` for the next.
4. Continue until the `record` module finishes the run.

The steps, in order, are:
- `steps/orient.md` — read `state/visits.json`, enumerate routines, apply the exclusion
  flag, keep only those with runs newer than `last_run_seen`.
- `steps/select-targets.md` — the three least recently RUN of those, oldest first.
- `steps/study-target.md` — read ONE target's recipe + recent runs; infer its intention.
- `steps/apply-lenses.md` — run the five lens modules on the target and apply safe fixes.
- `steps/fresh-eyes.md` — first-time-reader pass over the target's recipe; de-clutter.
- `steps/record.md` — commit targets, update visits, LEDGER, finish.

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
