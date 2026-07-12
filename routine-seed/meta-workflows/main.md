---
name: 'Meta: workflow library'
slug: meta-workflows
materialized_from:
  slug: meta-workflows
  commit: 4567d70
  version: 3
modules:
- apply-small-edits
- cluster-findings
- draft-new-workflow
- ingest-evidence
- orient
- propose-big-changes
- record
includes:
- ask-policy
- global-utils
- ledger-discipline
- improve-bugfix
- improve-research
- improve-features
- improve-ui
- improve-efficiency
tags:
- meta
- maintenance
---

You maintain the workflow library at `~/.local/share/routine-scheduler-libraries/workflows` by sweeping the
run transcripts of every routine under `~/routines` and turning what you find into safe
edits, proposals, or drafts.

This is a state machine. Do not hold the whole flow in your head — read one state's module,
do exactly what it says, then advance.

## Run flow

1. Read `state/phase.json` (`{step: <name>, cursor: {...}}`). If it is missing or empty,
   start at `orient`.
2. `read_file` the module for the current step from `steps/<step>.md` and follow it. Each
   module ends by telling you the next step and what to write back into `state/phase.json`.
3. Continue until the `record` module finishes the run.

The steps, in order, are:
- `steps/orient.md` — read `state/last_seen.json`, enumerate routines and their new runs.
- `steps/ingest-evidence.md` — spawn per-routine sub-workflows to read transcripts + LEDGERs.
- `steps/cluster-findings.md` — group findings by workflow slug; split defects from routine-local issues.
- `steps/apply-small-edits.md` — make lint-gated wording fixes, version-bump, git-sync.
- `steps/propose-big-changes.md` — write `proposals/`, file one deferred question each.
- `steps/draft-new-workflow.md` — draft a `status: draft` workflow when a shape has no fit.
- `steps/record.md` — advance `last_seen.json`, append the LEDGER, finish.

Phase model is **steady**: every run is the same full sweep, no cross-run milestones. The
step names above are just positions within one sweep, tracked in `state/phase.json` so a
resumed run continues where it stopped.

## Completion criteria
- `state/last_seen.json` advanced for every routine touched.
- Every finding is either fixed (committed to the library), proposed (file in `proposals/`
  plus a deferred `ask_user` question), or explicitly dropped with a reason in the LEDGER.
- The `record` module has appended this run's LEDGER entry and produced a
  findings → actions summary.

## Standing practices

These practice modules are this routine's own adapted standards — read each with read_file before the situation it governs, and refine them as you learn:
- `traits/ask-policy.md` — when and how to involve the user
- `traits/global-utils.md` — your tools, and how to use them
- `traits/improve-bugfix.md` — find and fix what's broken or wrong
- `traits/improve-efficiency.md` — a leaner process and tidier files
- `traits/improve-features.md` — grow what the routine delivers
- `traits/improve-research.md` — sharpen the routine's inputs and knowledge
- `traits/improve-ui.md` — the artifacts the user actually reads
- `traits/ledger-discipline.md` — the routine's memory of its own changes

After the main work, before finish, run each improve pass in its own module: `traits/improve-bugfix.md`, `traits/improve-efficiency.md`, `traits/improve-features.md`, `traits/improve-research.md`, `traits/improve-ui.md`.
