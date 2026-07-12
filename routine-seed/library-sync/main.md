---
name: Library sync
slug: library-sync
materialized_from:
  slug: library-sync
  commit: 4567d70
  version: 3
modules:
- export-instance
- record-and-report
- sync-repo
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

# Routine: Sync the instance to its library repo

Publish everything this instance has acquired to the one library repository and keep it in
sync with its remote: stage the routines + sanitized server config into the repo tree
(`instance-export`), then commit/pull/push the repo (`git-sync`), then report.

The library repo path:
- `~/.local/share/routine-scheduler-libraries` (one repo holding workflows/, fragments/,
  utils/ — and, after the export, routines/ and config/)

## Run flow

This routine is a state machine. Do not act from memory — always drive from the
persisted phase.

1. Read `state/phase.json` to get the current state `name`.
2. `read_file` the matching module under `steps/<name>.md` and follow it exactly.
3. When a module says to advance, write the next state into `state/phase.json`,
   then read + follow that module.

There is a single phase — **only** — every run is the same sweep. Its states run
in this order:

- `steps/export-instance.md` — run `instance-export` to stage routines + sanitized config
  into the repo tree, and read the result.
- `steps/sync-repo.md` — run `git-sync` on the repo and read the result.
- `steps/record-and-report.md` — append the LEDGER line and finish with the outcome.

Start at `export-instance` if `state/phase.json` has no current state.

## Completion criteria

- `instance-export` has run against the repo tree (or its error was recorded, without any
  repair attempt).
- The repo has been sync'd via `git-sync` (or its conflict reported without any resolution
  attempt).
- The run finished with a one-line summary: what was exported, and one repo status of
  pulled / pushed / up-to-date / conflict. `ok` when both calls succeeded; `partial` when
  either reported an error or a conflict.

## Standing practices

These practice modules are this routine's own adapted standards — read each with read_file before the situation it governs, and refine them as you learn:
- `traits/global-utils.md` — your tools, and how to use them
- `traits/ledger-discipline.md` — the routine's memory of its own changes
