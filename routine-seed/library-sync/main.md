---
name: Library sync
slug: library-sync
materialized_from:
  slug: library-sync
  commit: 4567d70
  version: 1
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

# Routine: Sync scheduler library repos

Keep the scheduler's three library repositories in sync with their remotes by
running the `git-sync` util on each, then report per-repo outcomes.

The three library repo paths (in order):
1. `~/.local/share/workflow-library`
2. `~/.local/share/routine-fragments`
3. `~/.local/share/global-utils`

## Run flow

This routine is a state machine. Do not act from memory — always drive from the
persisted phase.

1. Read `state/phase.json` to get the current state `name`.
2. `read_file` the matching module under `steps/<name>.md` and follow it exactly.
3. When a module says to advance, write the next state into `state/phase.json`,
   then read + follow that module.

There is a single phase — **only** — every run is the same sweep. Its states run
in this order:

- `steps/sync-sweep.md` — run `git-sync` on each of the three repos and read each result.
- `steps/record-and-report.md` — append the LEDGER line and finish with a per-repo summary.

Start at `sync-sweep` if `state/phase.json` has no current state.

## Completion criteria

- Each of the three library repos has been sync'd via `git-sync` (or its conflict
  reported without any resolution attempt).
- The run finished `ok` with a one-line-per-repo summary listing, for each path,
  one of: pulled / pushed / up-to-date / conflict.