---
name: Library sync
slug: library-sync
materialized_from:
  slug: library-sync
  commit: 4567d70
  version: 2
modules:
- record-and-report
- sync-sweep
includes:
- global-utils
- ledger-discipline
tags:
- meta
- maintenance
- sync
---

---
name: sync-scheduler-libraries
phase_file: state/phase.json
---

# Routine: Sync scheduler library repo

Keep the scheduler's merged library repository in sync with its remote by running
the `git-sync` util on it, then report the outcome.

The library repo path:
- `~/.local/share/routine-scheduler-libraries` (one repo holding workflows/, fragments/, utils/)

## Run flow

This routine is a state machine. Do not act from memory — always drive from the
persisted phase.

1. Read `state/phase.json` to get the current state `name`.
2. `read_file` the matching module under `steps/<name>.md` and follow it exactly.
3. When a module says to advance, write the next state into `state/phase.json`,
   then read + follow that module.

There is a single phase — **only** — every run is the same sweep. Its states run
in this order:

- `steps/sync-sweep.md` — run `git-sync` on the merged library repo and read the result.
- `steps/record-and-report.md` — append the LEDGER line and finish with the outcome.

Start at `sync-sweep` if `state/phase.json` has no current state.

## Completion criteria

- The merged library repo has been sync'd via `git-sync` (or its conflict reported
  without any resolution attempt).
- The run finished `ok` with a one-line summary of the outcome: one of
  pulled / pushed / up-to-date / conflict.
