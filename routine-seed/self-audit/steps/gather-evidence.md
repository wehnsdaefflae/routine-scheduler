# Step: gather-evidence (read-only)

Collect the raw signal. Touch nothing; keep your own context small by spawning parallel readers.

## A. Scheduler code changes since the anchor
- Use a util to git-log the repo since `last_commit`. If no such util exists, `write_util` a
  small one: `git -C /home/mark/git-repos/routine-scheduler log <last_commit>..HEAD --stat`
  (first run: log the recent history broadly, e.g. last ~20 commits).
- Capture: which files changed, churn hot-spots, any file now over the ~350-line one-responsibility
  budget, changes that touch contracts (action schema, transcript `EVENT_TYPES`, CLAUDE.md).

## B. Routine runtime behaviour (`/home/mark/routines`)
- For each routine under the routines home, **skip dot-dirs and this routine itself**.
- For runs newer than `last_ts`, read the top-level `transcript.jsonl` + `LEDGER.md` (+ peek
  `status.json`). **Cap the newest ~5 runs each.**
- **`spawn` parallel readers** (prompt = the file paths + the rubric below), then `wait` all.
- Rubric — collect per run: finish outcome (authored vs budget-forced), schema-retry storms,
  repeated-action warnings, fabrication-guard rejections, wasted turns, questions asked
  (answered vs ignored), and any workflow-vs-run conflicts.

## C. Health signals
- Daemon journal: a util over `journalctl --user -u routine-scheduler.service` for
  errors / overruns / orphaned runs since `last_ts`.
- Tests: `util pytest-run /home/mark/git-repos/routine-scheduler` — record pass/fail + tail.
- Lint: `util` running the repo's configured lint — record status.

## Next
Write `state/phase.json` = `{"state": "analyse-findings"}` and read `steps/analyse-findings.md`.
