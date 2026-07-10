# State: sync-repo

Sync the library repo with a single `git-sync` call. Do NOT resolve conflicts here.

## Do

Run the util action `git-sync` once, with args
`["~/.local/share/routine-scheduler-libraries", "-m", "instance sync", "--json"]`.

The call commits everything in the tree — workflows, fragments, utils, and the routines/ +
config/ just staged by `instance-export` — pulls (rebase) from the remote, and pushes.

## Read the result

From the JSON result, determine the outcome:

- **pulled** — new commits came in from the remote.
- **pushed** — local commits were pushed to the remote.
- **up-to-date** — nothing to pull and nothing to push.
- **conflict** — the result contains a `pull_error` (rebase conflict).

The repo can be both pulled and pushed — record both.

## On conflict

If the result reports `pull_error`, do NOT attempt to resolve it: no manual rebase,
no reset, no force-push, no edits in the repo. Just record it as a conflict and
carry its message forward for the summary.

## Advance

After the `git-sync` call has run and its outcome is noted, set `state/phase.json`
current state to `record-and-report` and follow that module.
