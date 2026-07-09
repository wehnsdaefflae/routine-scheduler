# State: record-and-report

Persist the sweep and report to the user.

## Do

1. Append ONE line to the LEDGER summarizing this sync sweep — e.g. which repos
   pulled, which pushed, which were up-to-date, and any conflicts. One line total.

2. Finish `ok` with a one-line-per-repo summary covering all three paths:

   - `~/.local/share/workflow-library` — <pulled / pushed / up-to-date / conflict>
   - `~/.local/share/routine-fragments` — <pulled / pushed / up-to-date / conflict>
   - `~/.local/share/global-utils` — <pulled / pushed / up-to-date / conflict>

   For any repo with a conflict, state that it hit a pull conflict and was left
   untouched for the user to resolve — include the `pull_error` detail.

## Completion criteria

- LEDGER has one new line for this sweep.
- Every one of the three repos appears in the summary with exactly one status
  (pulled/pushed/up-to-date/conflict), and no conflict was resolved.
