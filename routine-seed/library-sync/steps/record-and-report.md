# State: record-and-report

Persist the sweep and report to the user.

## Do

1. Append ONE line to the LEDGER summarizing this sweep: what `instance-export` staged
   (routines exported / files pruned / config yes-no, or its error) and what `git-sync` did
   (pulled / pushed / up-to-date / conflict). One line total.

2. Finish with a one-line summary of both calls, e.g.:

   - exported <n> routines + config; repo <pulled / pushed / up-to-date / conflict>

   Status: `ok` when both calls succeeded (up-to-date counts as success); `partial` when the
   export reported an error or the sync hit a conflict. If it hit a conflict, state that the
   repo was left untouched for the user to resolve — include the `pull_error` detail.

## Completion criteria

- LEDGER has one new line for this sweep, covering both the export and the sync.
- The summary reports the export outcome and exactly one repo status
  (pulled/pushed/up-to-date/conflict), and no conflict was resolved.
