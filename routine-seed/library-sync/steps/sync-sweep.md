# State: sync-sweep

Sync each library repo, one `git-sync` call per repo. Do NOT resolve conflicts here.

## Do

Run the util action `git-sync` once for each path below, in order:

1. `git-sync` with args `["~/.local/share/workflow-library", "--json"]`
2. `git-sync` with args `["~/.local/share/routine-fragments", "--json"]`
3. `git-sync` with args `["~/.local/share/global-utils", "--json"]`

Each call commits local changes, pulls (rebase) from the remote, and pushes.

## Read each result

From each JSON result, determine the repo's outcome:

- **pulled** — new commits came in from the remote.
- **pushed** — local commits were pushed to the remote.
- **up-to-date** — nothing to pull and nothing to push.
- **conflict** — the result contains a `pull_error` (rebase conflict).

A single repo can be both pulled and pushed — record both.

## On conflict

If a repo reports `pull_error`, do NOT attempt to resolve it: no manual rebase,
no reset, no force-push, no edits in the repo. Just record it as a conflict and
carry its message forward for the summary.

## Advance

After all three `git-sync` calls have run and their outcomes are noted, set
`state/phase.json` current state to `record-and-report` and follow that module.
