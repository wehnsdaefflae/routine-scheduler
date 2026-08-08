---
name: Library sync
slug: library-sync
materialized_from:
  slug: hand-authored
  commit: ''
  version: 1
---

You publish everything this instance has acquired to its one library repository, and keep that
repository in step with its remote. Two operations, in order, every run: stage the instance's
own state into the repo's working tree, then commit and sync the repo.

The library repo is `~/.local/share/routine-scheduler-libraries` — one git repo already holding
`workflows/`, `rules/`, `permissions/`, `playbooks/` and `utils/`, and gaining `routines/` and
`config/` from you. Its remote is the GitHub repository the instance is configured against.

This is a MECHANICAL job and your value is in the exceptions. The happy path is two calls and a
LEDGER line; do not narrate it, do not improve it, and do not repair the repository. A conflict,
a rejected push or a refused credential is a REPORT, not a puzzle to solve — resolving history
on a repo that is the only off-box copy of this instance is exactly the wrong place to be
inventive.

This is a state machine. Do not hold the whole flow in your head — read one state's module, do
exactly what it says, then advance.

## Run flow

Read `state/phase.json` (`{phase: <stage>}`) for the current stage; if missing or empty, start
at `export`. `read_file` that stage's module (`stages/<stage>.md`) and follow it — each ends by
naming the next stage and what to write back into `state/phase.json`.

1. **export** — mirror this instance's routines and its sanitized server config into the repo's
   working tree. Read the result; do not re-run it to "make sure".
2. **sync** — commit the working tree, bring the remote's history in, and push. Read the result.
3. **record** — append the LEDGER entry and finish with what actually happened.

Phase model is **steady**: every run is the same two operations.

## Completion criteria
- The export ran against the repo tree, or its error is recorded verbatim with no repair
  attempted.
- The repo was committed and pushed, or the failure is recorded verbatim and reported to
  whoever owns it — never worked around.
- This run has its LEDGER entry, and the finish summary says whether the off-box copy is
  current. A sync that did not push is a FAILED run, however cleanly the export went.

## Standing practices

These general rules bind this routine. Each states a principle, not a procedure — read one with read_rule before the situation it governs and apply it to the case in front of you:
- `ask-policy` — when and how to involve the user
- `decision-record` — keep the reasoning the artefacts cannot carry
- `evidence-discipline` — every claim traced to an observation
- `problem-routing` — send a problem to whoever owns it, not upward
- `root-cause-fix` — repair the cause, never the symptom
