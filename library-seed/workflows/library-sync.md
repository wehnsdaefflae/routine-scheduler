---
name: Library sync
slug: library-sync
description: Keep the scheduler's library repos (workflows, fragments, global utils) in sync with their remotes.
when_to_use: >
  Internal maintenance routine: pulls remote updates into each local library repo and pushes
  local changes, so libraries stay consistent across machines. Not a template for user tasks.
version: 1
status: stable
params: []
default_budgets: {max_turns: 12, max_wall_clock_min: 15}
requires: {schema_output: false}
includes: [global-utils, ledger-discipline]
---

## Run flow
1. For EACH library repo path in the instruction, run the `git-sync` util on it (util action,
   name `git-sync`, args = [the path, "--json"]). It commits local changes, pulls remote
   updates (rebase), and pushes — one call per repo.
2. Read each result: note whether it pulled new commits, pushed, or hit a pull conflict
   (`pull_error`). If a repo reports a conflict, do NOT try to resolve it — record it in the
   summary for the user.
3. Append one LEDGER line summarizing what synced.
4. Finish ok with a one-line-per-repo summary (pulled / pushed / up-to-date / conflict).

## Phases
- **only** — every run is the same sweep.

## Completion criteria
- Each library repo has been sync'd (or its conflict reported); the run summary lists each.
