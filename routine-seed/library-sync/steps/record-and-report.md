# State: record-and-report

Persist the sweep and report to the user.

## Do

1. Append ONE line to the LEDGER summarizing this sync sweep — whether the repo
   pulled, pushed, was up-to-date, or hit a conflict. One line total.

2. Finish `ok` with a one-line summary for the merged library repo:

   - `~/.local/share/routine-scheduler-libraries` — <pulled / pushed / up-to-date / conflict>

   If it hit a conflict, state that it hit a pull conflict and was left untouched
   for the user to resolve — include the `pull_error` detail.

## Completion criteria

- LEDGER has one new line for this sweep.
- The summary reports exactly one status (pulled/pushed/up-to-date/conflict), and
  no conflict was resolved.
